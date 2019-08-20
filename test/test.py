import sys

import os

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, ROOT)

from botleague_helpers.db import get_db
from box import Box
from problem_constants import constants

import utils
from problem_constants.constants import JOB_STATUS_CREATED, \
    INSTANCE_STATUS_USED
from eval_manager import EvaluationManager
from singleton_loop import SingletonLoop, STATUS, REQUESTED, RUNNING, STOPPED
from logs import log


def test_singleton_loop_local():
    singleton_loop_helper(use_firestore=False)


def test_singleton_loop_firestore():
    singleton_loop_helper(use_firestore=True)


def test_singleton_loop_exception():
    _, name = get_test_loop_stuff()

    def loop_fn():
        raise RuntimeError('Should be caught')
    loop = SingletonLoop(name, loop_fn, force_firestore_db=False)
    loop.db.set(STATUS, STOPPED)
    loop.run()
    assert loop.caught_exception


def test_singleton_loop_orphaned_runner():
    loop_fn, name = get_test_loop_stuff()
    loop1 = SingletonLoop(name, loop_fn, force_firestore_db=False)
    loop2 = SingletonLoop(name, loop_fn, force_firestore_db=False)
    loop1.db.set(STATUS, RUNNING + loop2.id)
    assert not loop1.obtain_semaphore(timeout=0)
    loop1.kill_now = True
    assert not loop1.obtain_semaphore(timeout=0)


def singleton_loop_helper(use_firestore):
    loop_fn, name = get_test_loop_stuff()
    loop1 = SingletonLoop(name, loop_fn, force_firestore_db=use_firestore)
    loop1.release_semaphore()
    assert loop1.semaphore_released()
    assert loop1.obtain_semaphore(timeout=0)
    assert loop1.db.get(STATUS) == RUNNING + loop1.id
    assert not loop1.semaphore_requested()  # no other loops yet
    loop2 = SingletonLoop(name, loop_fn, force_firestore_db=use_firestore)
    assert not loop2.obtain_semaphore(timeout=0)  # loop1 needs to grant first
    assert loop1.semaphore_requested().startswith(REQUESTED)
    assert loop1.semaphore_released()
    assert loop2.granted_semaphore()
    loop1.db.delete_all_test_data()


def get_test_loop_stuff():
    def loop_fn():
        print('yoyoyo')

    name = 'test_loop_' + utils.generate_rand_alphanumeric(32)
    return loop_fn, name


def test_job_trigger():
    # Mark test job as to start
    test_id = utils.generate_rand_alphanumeric(32)
    test_jobs_collection = 'test_jobs_' + test_id
    test_instances_collection = 'test_instances_' + test_id
    jobs_db = get_db(test_jobs_collection, use_boxes=True,
                     force_firestore_db=True)
    instances_db = get_db(test_instances_collection, use_boxes=True,
                          force_firestore_db=True)

    job_id = 'TEST_JOB_' + utils.generate_rand_alphanumeric(32)

    trigger_job(instances_db, job_id, jobs_db,
                botleague_liaison_host=constants.BOTLEAGUE_LIAISON_HOST)


def manually_trigger_job():
    job_id = 'TEST_JOB_' + utils.generate_rand_alphanumeric(32)
    trigger_job(instances_db=None, job_id=job_id, jobs_db=None,
                botleague_liaison_host='https://a3d66072.ngrok.io',
                docker_tag='deepdriveio/deepdrive:bot_domain_randomization')


def trigger_job(instances_db, job_id, jobs_db, botleague_liaison_host, docker_tag=None):
    docker_tag = docker_tag or 'deepdriveio/problem-worker-test'
    eval_mgr = EvaluationManager(jobs_db=jobs_db, instances_db=instances_db)
    eval_mgr.check_for_finished_jobs()
    test_job = Box(botleague_liaison_host=botleague_liaison_host,
                   status=JOB_STATUS_CREATED,
                   id=job_id,
                   eval_spec=Box(
                       docker_tag=docker_tag,
                       eval_id=job_id,
                       eval_key='fake_eval_key',
                       seed=1,
                       problem='domain_randomization',
                       pull_request=None,
                       max_seconds=20))

    try:
        eval_mgr.jobs_db.set(job_id, test_job)
        new_jobs = eval_mgr.trigger_jobs()
        if new_jobs:
            # We don't actually start instances but we act like we did.
            assert new_jobs[0].status == JOB_STATUS_CREATED or \
                   new_jobs[0].instance_id

            if 'instance_id' in new_jobs[0]:
                instance_meta = eval_mgr.instances_db.get(
                    new_jobs[0].instance_id)

                # So we have real instance meta, but inserted the job into a
                # test collection that the instance is not watching.
                # So the job will not actually run.
                assert instance_meta.status == INSTANCE_STATUS_USED
        else:
            log.warning('Test did not find an instance to run. TODO: use'
                        ' test instance data.')
    finally:
        if jobs_db is not None:
            jobs_db.delete_all_test_data()
        if instances_db is not None:
            instances_db.delete_all_test_data()


def run_all(current_module):
    print('running all tests')
    for attr in dir(current_module):
        if attr.startswith('test_'):
            print('running ' + attr)
            getattr(current_module, attr)()


def main():
    current_module = sys.modules[__name__]
    if len(sys.argv) > 1:
        test_case = sys.argv[1]
        getattr(current_module, test_case)()
    else:
        run_all(current_module)


if __name__ == '__main__':
    main()
