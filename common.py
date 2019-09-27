from botleague_helpers.db import get_db

from problem_constants import constants


STACKDRIVER_LOG_NAME = 'deepdrive-coordinator'

def get_jobs_db():
    return get_db(constants.JOBS_COLLECTION_NAME)


def get_worker_instances_db(force_firestore_db=False):
    return get_db(constants.WORKER_INSTANCES_COLLECTION_NAME,
                  force_firestore_db=force_firestore_db)
