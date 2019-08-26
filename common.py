from botleague_helpers.db import get_db

from problem_constants import constants


def get_jobs_db():
    return get_db(constants.JOBS_COLLECTION_NAME)


def get_worker_instances():
    return get_db(constants.WORKER_INSTANCES_COLLECTION_NAME)
