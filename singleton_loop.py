import signal
import time

import traceback
from typing import Union

from botleague_helpers.db import get_db

import constants
import utils
from eval_manager import EvaluationManager
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
        self.id = utils.generate_rand_alphanumeric(10)
        self.previous_status = None
        signal.signal(signal.SIGINT, self.exit_gracefully)
        signal.signal(signal.SIGTERM, self.exit_gracefully)

    def run(self):
        self.obtain_semaphore()
        log.success(f'Running {self.loop_name}, loop_id: {self.id}')
        while not self.semaphore_released():
            if self.kill_now:
                self.release_semaphore()
                return
            else:
                self.fn()
                time.sleep(1)
                # TODO: Ping cronitor every minute

    def obtain_semaphore(self, timeout=None):
        start = time.time()
        # TODO: Avoid polling by creating a Firestore watch and using a
        #   mutex to avoid multiple threads processing the watch.
        if self.db.get(STATUS) == STOPPED:
            self.db.set(STATUS, RUNNING + self.id)
            return True
        self.request_semaphore()
        # TODO: Check for a third loop that requested access and alert, die,
        #  or re-request. As-is we just zombie.
        while not self.granted_semaphore():
            log.info('Waiting for other eval loop to end')
            if self.kill_now:
                if self.db.compare_and_swap(STATUS, REQUESTED + self.id,
                                            self.previous_status):
                    # Other loop never saw us, good!
                    return False
                else:
                    # We have problems
                    if self.db.get(STATUS) == GRANTED + self.id:
                        # Other loop beat us in a race to set status
                        # and will die!
                        self.release_semaphore()
                        # TODO: Create an alert from this log.
                        log.error(f'No {self.id} running! Needs manual start')
                    else:
                        # Could be that a third loop requested.
                        self.release_semaphore()
                        # TODO: Create an alert from this log.
                        log.error(f'Race condition encountered in {self.id} '
                                  f'Needs manual start')
            elif timeout is not None and time.time() - start > timeout:
                return False
            else:
                time.sleep(1)
        log.info('Waiting for other eval loop to end')
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
            log.warning(f'Found orphaned {self.id} after requesting! '
                        f'Did a race condition occur?')
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

    def exit_gracefully(self, signum, frame):
        log.info(f'Exiting gracefully from {signum} {frame}')
        self.kill_now = True

