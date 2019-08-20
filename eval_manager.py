import time
from typing import Optional

import googleapiclient.discovery
import os

import requests
from botleague_helpers.config import blconfig, in_test
from box import Box, BoxList
from botleague_helpers.db import DB
from google.cloud.firestore_v1 import SERVER_TIMESTAMP
from problem_constants import constants

from problem_constants.constants import INSTANCE_STATUS_AVAILABLE, \
    INSTANCE_STATUS_USED, JOB_STATUS_CREATED, GCP_ZONE, GCP_PROJECT, \
    INSTANCE_EVAL_LABEL, SUPPORTED_PROBLEMS, ROOT, INSTANCE_CONFIG_PATH, \
    INSTANCE_NAME_PREFIX, MAX_EVAL_INSTANCES, JOB_STATUS_RUNNING, \
    JOB_STATUS_FINISHED, JOB_STATUS_ASSIGNED, JOB_STATUS_TIMED_OUT, \
    JOB_STATUS_DENIED_CONFIRMATION, JOB_STATUS_CONFIRMED
from common import get_jobs_db, get_instances_db
from logs import log

# TODO:
#   [x] We get a call from BL with the eval_id
#   For problem and bot container
#   [x] Store the job information in Firestore with the instance-id before starting instance (get from instance creation or from DB on startup)
#      Here's how you get the instance id on an instance curl "http://metadata.google.internal/computeMetadata/v1/instance/id" -H "Metadata-Flavor: Google"
#   [x] Start instance in the loop, if none available, create.
#   Start a problem AND bot instance
#   If this is a CI run, we'll have to build and push the container first.
#   [x] If an instance is already created but stopped, then start it
#   [x] If an instance is already started (check gcloud api with list filter) and available (which can be determined by querying firestore), set Firestore's new job data with instance id
#   [x] Instances will have run a worker which checks Firestore for jobs using its instance id
#   [x] All calls in loop should be async, just one sleep at the end.
#   Set results in Firestore on worker when job is complete then we'll forward to BOTLEAGUE_CALLBACK (for problems only, not bots!).
#   [x] If the container process ends with a non-zero exit status, the worker process will set an error in the results in Firestore
#   To detect failed instances, slowly query instance state (once per minute) as most the time it will be fine.
#   Stop instances after results sent with idle_timeout.
#   Delete/Kill instances if over threshold of max instances. Meaure start/create over a week, maybe we can just create.

"""
# The number of queries are limited to a maximum of 10 queries per minute per VM instance.
# Queries do not exceed a burst of 3 queries per second. If this maximum rate is exceeded,
 Compute Engine might arbitrarily remove guest attributes that are in the process of being written.
 This data removal is needed to ensure that other critical system data can be written to the server.
"""

# gcloud compute instances add-metadata INSTANCE \
#   --metadata bread=mayo,cheese=cheddar,lettuce=romaine
# gcloud compute instances add-metadata [INSTANCE_NAME] --metadata enable-guest-attributes=TRUE
#


class EvaluationManager:
    """
    The evaluation endpoint implementation for the Deepdrive Problem Endpoint.

    - `problem` is the string identifier for the problem.
    - `eval_id` is the unique identifier for this evaluation run.
    - `eval_key` is the evaluation key to pass back to the Botleague liaison.
    - `seed` is the seed to use for random number generation.
    - `docker_tag` is the tag for the bot container image.
    - `pull_request` is the relevant pull request details, or None.
    """

    def __init__(self, jobs_db=None, instances_db=None):
        self.gce_ops_in_progress = BoxList()
        self.instances_db = instances_db or get_instances_db()
        self.jobs_db: DB = jobs_db or get_jobs_db()
        self.gce = googleapiclient.discovery.build('compute', 'v1')
        self.project: str = GCP_PROJECT
        self.zone: str = GCP_ZONE
        self.last_cronitor_ping_time = -1

    def loop(self):
        now = time.time()
        self.last_cronitor_ping_time = ping_cronitor(
            now, self.last_cronitor_ping_time, 'run')

        self.trigger_jobs()
        self.check_gce_ops_in_progress()
        self.check_jobs_in_progress()
        # TODO: self.stop_idle_instances()
        # TODO: restart instances that have been evaluating for more than
        #  problem timeout
        # TODO: self.delete_idle_instances_over_threshold()

        self.last_cronitor_ping_time = ping_cronitor(
            now, self.last_cronitor_ping_time, 'complete')

    def trigger_jobs(self) -> BoxList:
        new_jobs = BoxList()
        for job in self.jobs_db.where('status', '==', JOB_STATUS_CREATED):
            try:
                log.info(f'Triggering job {job.to_json(indent=2)}')
                job = self.trigger_eval(job)
            except:
                # Could have been a network failure, so just try again.
                # More granular exceptions should be handled before this
                # which can set the job to not run again
                # if that's what's called for.

                log.exception(f'Exception triggering eval for job {job}, '
                              f'will try again shortly.')

            if job:
                new_jobs.append(job)

            #  Create instance
            #  Start instance
            # TODO: Check for failed / crashed instance once per minute
            # TODO: Stop instances if they have been idle for longer than timeout
            # TODO: Cap total instances
            # TODO: Cap instances per bot owner, using first part of docker tag
            # TODO: Delete instances over threshold of stopped+started

        return new_jobs

    def check_jobs_in_progress(self):
        for job in self.jobs_db.where('status', '==', JOB_STATUS_RUNNING):
            max_seconds = Box(job, default_box=True).eval_spec.max_seconds
            if not max_seconds:
                log.warning('No max_seconds in problem definition, defaulting'
                            ' to 5 minutes')
                max_seconds = 60 * 5

            if time.time() - job.started_at.timestamp() > max_seconds:
                log.error(f'Job {job} took longer than {max_seconds} seconds, '
                          f'should stop instance: {job.instance_id}!')
                job.status = JOB_STATUS_TIMED_OUT
                self.jobs_db.set(job.id, job)

                # TODO: Move this into problem-constants and rename
                #  problem-helpers as it's shared with problem-worker
                instance = self.instances_db.get(job.instance_id)
                if instance.status != constants.INSTANCE_STATUS_AVAILABLE:
                    instance.status = constants.INSTANCE_STATUS_AVAILABLE
                    instance.time_last_available = SERVER_TIMESTAMP
                    self.instances_db.set(job.instance_id, instance)
                    log.success(f'Made instance {job.instance_id} available')

                # self.gce.instances()
                # TODO: Stop the instance in case there's an issue with the
                #  instance itself
                # TODO: Set job error timeout
                pass

    def check_for_finished_jobs(self):
        # TODO: Make this more efficient by querying instances or just
        #   disable or don't do this at all in the loop
        #   since callback will do it for us.
        for job in self.jobs_db.where('status', '==', JOB_STATUS_FINISHED):
            instance = self.instances_db.get(job.instance_id)
            if instance.status == INSTANCE_STATUS_USED:
                instance.status = INSTANCE_STATUS_AVAILABLE
                self.instances_db.set(job.instance_id, instance)

    def check_gce_ops_in_progress(self):
        ops_still_in_progress = BoxList()
        for op in self.gce_ops_in_progress:

            try:
                op_result = Box(self.gce.zoneOperations().get(
                    project=self.project,
                    zone=self.zone,
                    operation=op.name).execute())
            except:
                log.exception('Could not get op_result')
                break
            if op_result.status == 'DONE':
                if 'error' in op_result:
                    log.error(
                        f'GCE operation resulted in an error: '
                        f'{op_result.error}\nOperation was:'
                        f'\n{op.to_json(indent=2)}')
                    if op.operationType == 'insert':
                        # Retry the creation?
                        pass
                    # elif op.operationType == 'start':
                    #
            else:
                ops_still_in_progress.append(op)
        self.gce_ops_in_progress = ops_still_in_progress

    def trigger_eval(self, job) -> Optional[Box]:
        if not self.confirm_evaluation(job):
            return
        problem = job.eval_spec.problem

        # Verify that the specified problem is supported
        if problem not in SUPPORTED_PROBLEMS:
            log.error(f'Unsupported problem "{problem}"')
            job.status = JOB_STATUS_DENIED_CONFIRMATION
            self.save_job(job)
            return

        eval_instances = self.list_instances(INSTANCE_EVAL_LABEL)

        if len(eval_instances) >= MAX_EVAL_INSTANCES:
            log.error(f'Over instance limit, waiting for instances to become '
                      f'available to run job {job.id}')
            return job

        stopped_instances = [inst for inst in eval_instances
                             if inst.status.lower() == 'terminated']

        started_instances = [inst for inst in eval_instances
                             if inst.status.lower() == 'running']
        for inst in started_instances:
            inst_meta = self.instances_db.get(inst.id)
            if not inst_meta or inst_meta.status == INSTANCE_STATUS_AVAILABLE:
                # Set the instance to used before starting the job in case
                # it calls back to /results very quickly before setting status.
                self.save_eval_instance(Box(id=inst.id, inst=inst,
                                            status=INSTANCE_STATUS_USED))
                self.assign_job_to_instance(inst, job)

                log.success(f'Marked job {job.id} to start on '
                            f'instance {inst.id} which was already running')
                break
        else:
            if stopped_instances:
                inst = stopped_instances[0]
                job.instance_id = inst.id
                self.assign_job_to_instance(inst, job)
                self.gce_ops_in_progress.append(self.start_instance(inst))
                log.success(
                    f'No running instances for job {job.id}, so started '
                    f'instance {inst.id} for it.')
            else:
                self.gce_ops_in_progress.append(self.create_instance(
                    current_instances=eval_instances))
                log.success(f'No running or stopped instances available for '
                            f'job {job.id}, so created new instance {job.id}')

        # TODO: Set DEEPDRIVE_SIM_HOST
        # TODO: Set network tags between bot and problem container for port 5557
        return job

    def confirm_evaluation(self, job) -> bool:
        if in_test():
            status = JOB_STATUS_CONFIRMED
            ret = True
        else:
            url = f'{job.botleague_liaison_host}/confirm'
            json = {'eval_key': job.eval_spec.eval_key}
            log.info(f'Confirming eval {json} at {url}...')
            confirmation = requests.post(url, json=json)
            if 400 <= confirmation.status_code < 500:
                status = JOB_STATUS_DENIED_CONFIRMATION
                log.error('Botleague denied confirmation of job, skipping')
                ret = False
            elif not confirmation.ok:
                status = JOB_STATUS_CREATED
                log.error('Unable to confirm job with botleague liaison, '
                          'will try again shortly')
                ret = False
            else:
                status = JOB_STATUS_CONFIRMED
                log.success(f'Confirmed eval {json} at {url}')
                ret = True
        job.status = status
        self.save_job(job)
        return ret

    def start_instance(self, inst):
        return self.gce.instances().start(
            project=self.project,
            zone=self.zone,
            instance=inst.name).execute()

    def assign_job_to_instance(self, instance, job):
        # TODO: Compare and swap
        job.status = JOB_STATUS_ASSIGNED
        job.instance_id = instance.id
        job.started_at = time.time()
        self.save_job(job)

    def save_eval_instance(self, eval_instance):
        self.instances_db.set(eval_instance.id, eval_instance)

    def save_job(self, job):
        job.id = job.eval_spec.eval_id  # The job id is the eval id
        self.jobs_db.set(job.id, job)
        return job.id

    def set_eval_data(self, inst, eval_spec):
        inst.eval_spec = eval_spec
        self.instances_db.set(inst.id, inst)

    def list_instances(self, label) -> BoxList:
        if label:
            query_filter = f'labels.{label}:*'
        else:
            query_filter = None
        ret = self.gce.instances().list(project=self.project,
                                        zone=self.zone,
                                        filter=query_filter).execute()
        ret = BoxList(ret.get('items', []))
        return ret

    def create_instance(self, current_instances):
        instance_name = self.get_next_instance_name(current_instances)
        config_path = os.path.join(ROOT, INSTANCE_CONFIG_PATH)
        config = Box.from_json(filename=config_path)
        config.name = instance_name
        config.disks[0].deviceName = instance_name
        create_op = Box(self.gce.instances().insert(
            project=self.project,
            zone=self.zone,
            body=config.to_dict()).execute())
        return create_op

    @staticmethod
    def get_next_instance_name(current_instances):
        current_instance_names = [i.name for i in current_instances]
        current_instance_indexes = []
        for name in current_instance_names:
            index = name[name.rindex('-')+1:]
            if index.isdigit():
                current_instance_indexes.append(int(index))
            else:
                log.warning('Instance with non-numeric index in name found '
                            + name)
        if not current_instance_indexes:
            next_index = 0
        else:
            next_index = max(current_instance_indexes) + 1
        instance_name = INSTANCE_NAME_PREFIX + str(next_index)
        return instance_name


def ping_cronitor(now, last_ping_time, state='run') -> int:
    if blconfig.is_test:
        ret = last_ping_time
    else:
        log.trace(f'Pinging cronitor with {state}')
        # Ping cronitor every minute
        requests.get('https://cronitor.link/MJ8I4x/%s' % state, timeout=10)
        ret = now
    return ret


def main():
    # compute = googleapiclient.discovery.build('compute', 'v1')
    # eval_instances = list_instances(compute, label='deepdrive-eval')
    eval_mgr = EvaluationManager()
    eval_mgr.trigger_jobs()


if __name__ == '__main__':
    main()
