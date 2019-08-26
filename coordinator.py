import requests
from botleague_helpers.config import in_test
from problem_constants import constants

from job_manager import JobManager
from singleton_loop import SingletonLoop
from logs import log


def main():
    job_manager = JobManager()
    job_manager.check_for_finished_jobs()

    def loop_fn():
        ping_cronitor('run')
        # ci_mgr.run()
        job_manager.run()
        ping_cronitor('complete')

    SingletonLoop(loop_name=constants.JOB_LOOP_ID,
                  fn=loop_fn).run()


def ping_cronitor(state):
    if in_test():
        return
    else:
        log.trace(f'Pinging cronitor with {state}')
        requests.get('https://cronitor.link/MJ8I4x/%s' % state, timeout=10)


if __name__ == '__main__':
    main()
