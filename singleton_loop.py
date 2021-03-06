import sys
from datetime import datetime
import signal
import time

from typing import Union

from botleague_helpers.config import in_test
from botleague_helpers.db import get_db
from box import Box

import utils
from logs import log

LOOP_POSTFIX = '-loop-id='
RUNNING = 'running' + LOOP_POSTFIX
GRANTED = 'granted' + LOOP_POSTFIX
REQUESTED = 'requested' + LOOP_POSTFIX
STOPPED = 'stopped'
STATUS = 'status'

# TODO: Move this to it's own package or to botleague_helpers


class SingletonLoop:
    def __init__(self, loop_name, fn, force_firestore_db=False):
        self.fn = fn
        self.loop_name = loop_name
        self.db = get_db(loop_name + '_semaphore', use_boxes=True,
                         force_firestore_db=force_firestore_db)
        self.kill_now = False
        self.caught_exception = False
        self.id = datetime.now().strftime(
            f'%Y-%m-%d__%I-%M-%S%p#'
            f'{utils.generate_rand_alphanumeric(3)}')
        self.previous_status = None
        self.caught_sigterm = False
        self.caught_sigint = False
        self.started_waiting_for_other_loop_time = None
        signal.signal(signal.SIGINT, self.handle_sigint)
        signal.signal(signal.SIGTERM, self.handle_sigterm)

    def run(self):
        if not self.obtain_semaphore():
            log.error('Could not obtain semaphore! Check to see if other loop '
                      'is running!')
            self.sleep_one_second()  # We'll be in a reboot loop until shutdown
            return
        log.success(f'Running {self.loop_name}, loop_id: {self.id}')
        while not self.semaphore_released():
            if self.kill_now:
                self.exit()
                return
            else:
                try:
                    self.fn()
                    self.sleep_one_second()
                except Exception:
                    self.kill_now = True
                    self.caught_exception = True
                    log.exception('Exception in loop, killing')

    def exit(self):
        self.release_semaphore()
        if in_test():
            log.info('Not exiting in test')
            return
        elif self.caught_exception:
            log.error('Exiting due to caught exception')
            status = 100
        elif self.caught_sigint:
            log.warning('Exiting due to caught sigint')
            status = 101
        elif self.caught_sigterm:
            log.warning('Exiting due to caught sigterm')
            status = 102
        else:
            log.error('Unexpected reason for exit')
            status = 1

        log.warning(f'Exiting with status {status}')

        # http://tldp.org/LDP/abs/html/exitcodes.html
        sys.exit(status)

    def sleep_one_second(self):
        time.sleep(1) if not (in_test() or self.kill_now) else None

    @log.catch
    def obtain_semaphore(self, timeout=None) -> bool:
        start = time.time()
        # TODO: Avoid polling by creating a Firestore watch and using a
        #   mutex to avoid multiple threads processing the watch.
        if self.db.get(STATUS) == Box():
            log.warning('No semaphore document found, creating one!')
            self.db.set(STATUS, RUNNING + self.id)
            return True
        elif self.db.get(STATUS) in [Box(), STOPPED]:
            self.db.set(STATUS, RUNNING + self.id)
            return True
        self.request_semaphore()
        # TODO: Check for a third loop that requested access and alert, die,
        #  or re-request. As-is we just zombie.
        while not self.granted_semaphore():
            log.info('Waiting for other loop to end')
            if self.started_waiting_for_other_loop_time is None:
                self.started_waiting_for_other_loop_time = time.time()
            if time.time() - self.started_waiting_for_other_loop_time > 5:
                log.error('Assuming previous loop died without releasing '
                          'semaphore. Setting to stopped.')
                self.db.set(STATUS, STOPPED)
                self.started_waiting_for_other_loop_time = None
            if self.kill_now:
                log.warning('Killing loop while requesting semaphore, '
                            'here be dragons!')
                if self.db.compare_and_swap(STATUS, REQUESTED + self.id,
                                            self.previous_status):
                    # Other loop never saw us, good!
                    return False
                else:
                    # We have problems
                    if self.db.get(STATUS) == GRANTED + self.id:
                        # Other loop beat us in a race to set status
                        # and released so thinks we own the semaphore.
                        self.release_semaphore()
                        # TODO: Create an alert from this log.
                        raise RuntimeError(f'No {self.id} running! '
                                           f'Needs manual start')
                    else:
                        # Could be that a third loop requested.
                        self.release_semaphore()
                        # TODO: Create an alert from this log.
                        raise RuntimeError(f'Race condition encountered in '
                                           f'{self.id} Needs manual start')
            elif timeout is not None and time.time() - start > timeout:
                return False
            else:
                time.sleep(1)
        return True

    def request_semaphore(self):
        self.previous_status = self.db.get(STATUS)
        self.db.set(STATUS, REQUESTED + self.id)

    def granted_semaphore(self):
        granted = self.db.compare_and_swap(
            key=STATUS,
            expected_current_value=GRANTED + self.id,
            new_value=RUNNING + self.id)
        found_orphan = self.db.compare_and_swap(
            key=STATUS,
            expected_current_value=STOPPED,
            new_value=RUNNING + self.id)
        if found_orphan:
            # TODO: Create an alert from this log
            log.warning(f'Found orphaned semaphore: {self.id}. '
                        f'This can happen when this process is forcefully '
                        f'killed. '
                        f'i.e. stopping the instance from the UI can cause '
                        f'this. You can run `make stop` to properly shut '
                        f'down the server.')
        ret = granted or found_orphan
        return ret

    def semaphore_released(self):
        # TODO: Avoid polling by creating a Firestore watch and using a
        #   mutex to avoid multiple threads processing the watch.
        req = self.semaphore_requested()
        if req:
            if req == STOPPED:
                log.info('Stop loop requested')
            elif req.startswith(REQUESTED):
                self.grant_semaphore(req)
                log.info('End loop requested, granted and stopping')
            else:
                log.info('Stopping for unexpected status %s' % req)
            return True
        else:
            return False

    def semaphore_requested(self) -> Union[bool, str]:
        status = self.db.get(STATUS)
        if status == RUNNING + self.id:
            return False
        else:
            log.info('Semaphore changed to %s, stopping' % status)
            if not status.startswith(REQUESTED) and status != STOPPED:
                log.error('Unexpected semaphore status %s' % status)
            return status

    def grant_semaphore(self, req):
        self.db.set(STATUS, req.replace(REQUESTED, GRANTED))
        log.info('Granted semaphore to %s' % req)

    def release_semaphore(self):
        self.db.set(STATUS, STOPPED)
        log.info(f'Released semaphore for {self.id}')

    def handle_sigint(self, signum=None, frame=None):
        log.warning(f'Sigint caught {signum} {frame}')
        self.kill_now = True
        self.caught_sigint = True

    def handle_sigterm(self, signum=None, frame=None):
        log.error(f'Sigterm caught {signum} {frame}')
        self.kill_now = True
        self.caught_sigterm = True

