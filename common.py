from botleague_helpers.db import get_db

from problem_constants import constants


def get_jobs_db():
    return get_db(constants.EVAL_JOBS_COLLECTION_NAME, use_boxes=True)


def get_instances_db():
    return get_db(constants.EVAL_INSTANCES_COLLECTION_NAME, use_boxes=True)
