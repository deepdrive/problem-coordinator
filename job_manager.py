from datetime import datetime, timedelta

import time
from typing import Optional, List, Tuple

# noinspection PyPackageRequirements
import googleapiclient.discovery
import os

import requests
from botleague_helpers.config import in_test
from box import Box, BoxList
from botleague_helpers.db import DB, get_db
from google.cloud.firestore_v1 import SERVER_TIMESTAMP
from problem_constants import constants

from problem_constants.constants import INSTANCE_STATUS_AVAILABLE, \
    INSTANCE_STATUS_USED, JOB_STATUS_CREATED, GCP_ZONE, GCP_PROJECT, \
    WORKER_INSTANCE_LABEL, SUPPORTED_PROBLEMS, INSTANCE_CONFIG_PATH, \
    INSTANCE_NAME_PREFIX, MAX_WORKER_INSTANCES, JOB_STATUS_RUNNING, \
    JOB_STATUS_FINISHED, JOB_STATUS_ASSIGNED, JOB_STATUS_TIMED_OUT, \
    JOB_STATUS_DENIED_CONFIRMATION, JOB_TYPE_EVAL, JOB_TYPE_SIM_BUILD, \
    LOCAL_INSTANCE_ID
from common import get_jobs_db, get_worker_instances_db
from logs import log
from utils import dbox, box2json, get_datetime_from_datetime_nanos

ROOT = os.path.dirname(os.path.realpath(__file__))
SHOULD_TIMEOUT_JOBS = False

# TODO:
#   To detect failed instances, slowly query instance state (once per minute) as most the time it will be fine.
#   Stop instances after results sent with idle_timeout.
#   Delete/Kill instances if over threshold of max instances. Measure start/create over a week, maybe we can just create.


class JobManager:
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
        self.instances_db: DB = instances_db or get_worker_instances_db()
        self.jobs_db: DB = jobs_db or get_jobs_db()
        self.gce = googleapiclient.discovery.build('compute', 'v1')
        self.project: str = GCP_PROJECT
        self.zone: str = GCP_ZONE

    def run(self):
        self.assign_jobs()
        self.check_gce_ops_in_progress()
        self.check_jobs_in_progress()
        self.check_for_idle_instances()
        # TODO: self.stop_idle_instances()
        # TODO: restart instances that have been evaluating for more than
        #  problem timeout
        # TODO: self.delete_idle_instances_over_threshold()

    def assign_jobs(self) -> Tuple[BoxList, List]:
        new_jobs = BoxList()
        exceptions = []
        for job in self.jobs_db.where('status', '==', JOB_STATUS_CREATED):
            try:
                if self.should_start_job(job):
                    log.info(f'Assigning job '
                             f'{job.to_json(indent=2, default=str)}...')
                    self.assign_job(job)
                    new_jobs.append(job)
            except Exception as e:
                # Could have been a network failure, so just try again.
                # More granular exceptions should be handled before this
                # which can set the job to not run again
                # if that's what's called for.

                log.exception(f'Exception triggering eval for job {job}, '
                              f'will try again shortly.')
                exceptions.append(e)

        # TODO: Check for failed / crashed instance once per minute
        # TODO: Stop instances if they have been idle for longer than timeout
        # TODO: Cap total instances
        # TODO: Cap instances per bot owner, using first part of docker tag
        # TODO: Delete instances over threshold of stopped+started

        return new_jobs, exceptions

    def check_jobs_in_progress(self):
        for job in self.jobs_db.where('status', '==', JOB_STATUS_RUNNING):
            if SHOULD_TIMEOUT_JOBS:
                # TODO: We need to stop the job if it's still running before
                #  returning the worker back to the instance pool
                self.handle_timed_out_jobs(job)

    def handle_timed_out_jobs(self, job):
        max_seconds = Box(job, default_box=True).eval_spec.max_seconds
        if not max_seconds:
            log.debug('No max_seconds in problem definition, using default')
            if job.job_type == JOB_TYPE_EVAL:
                max_seconds = 60 * 5
            elif job.job_type == JOB_TYPE_SIM_BUILD:
                max_seconds = 60 * 10
            else:
                log.error(f'Unexpected job type {job.job_type} for job: '
                          f'{job}')
                return
        if time.time() - job.started_at.timestamp() > max_seconds:
            log.error(f'Job {job} took longer than {max_seconds} seconds, '
                      f'consider stopping instance: {job.instance_id} '
                      f'in case the instance is bad.')
            job.status = JOB_STATUS_TIMED_OUT
            self.jobs_db.set(job.id, job)
            self.make_instance_available(job.instance_id)
            # TODO: Stop the instance in case there's an issue with the
            #  instance itself
            # TODO: Set job error timeout
            pass

    def make_instance_available(self, instance_id):
        # TODO: Move this into problem-constants and rename
        #  problem-helpers as it's shared with problem-worker
        instance = self.instances_db.get(instance_id)
        if instance.status != constants.INSTANCE_STATUS_AVAILABLE:
            instance.status = constants.INSTANCE_STATUS_AVAILABLE
            instance.time_last_available = SERVER_TIMESTAMP
            self.instances_db.set(instance_id, instance)
            log.info(f'Made instance {instance_id} available')
        else:
            log.warning(f'Instance {instance_id} already available')

    def check_for_finished_jobs(self):
        # TODO: Make this more efficient by querying instances or just
        #   disable or don't do this at all in the loop
        #   since callback will do it for us.

        try:
            for job in self.jobs_db.where('status', '==', JOB_STATUS_FINISHED):
                if 'instance_id' in job:
                    inst_id = job.instance_id
                    if inst_id != LOCAL_INSTANCE_ID:
                        instance = self.instances_db.get(inst_id)
                        if not instance:
                            log.warning(
                                f'Instance "{inst_id}" not found for job:\n'
                                f'{job.to_json(indent=2, default=str)}')
                        elif instance.status == INSTANCE_STATUS_USED:
                            self.make_instance_available(inst_id)
        except:
            log.exception('Unable to check for finished jobs')

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
                        f'\n{op.to_json(indent=2, default=str)}')
                    if op.operationType == 'insert':
                        # Retry the creation?
                        pass
                    # elif op.operationType == 'start':
                    #
            else:
                ops_still_in_progress.append(op)
        self.gce_ops_in_progress = ops_still_in_progress

    def assign_job(self, job) -> Optional[Box]:
        if dbox(job).run_local_debug:
            log.warning(f'Run local debug is true, setting instance id to '
                        f'{constants.LOCAL_INSTANCE_ID}')
            self.assign_job_to_instance(constants.LOCAL_INSTANCE_ID, job)
            return job

        worker_instances = self.get_worker_instances()

        self.prune_terminated_instances(worker_instances)

        # https://cloud.google.com/compute/docs/instances/instance-life-cycle

        provisioning_instances = [inst for inst in worker_instances
                                  if inst.status.lower() == 'provisioning']

        staging_instances = [inst for inst in worker_instances
                             if inst.status.lower() == 'staging']

        started_instances = [inst for inst in worker_instances
                             if inst.status.lower() == 'running']

        # TODO: Handle these
        stopping_instances = [inst for inst in worker_instances
                             if inst.status.lower() == 'stopping']

        # TODO: Handle these
        repairing_instances = [inst for inst in worker_instances
                             if inst.status.lower() == 'repairing']

        stopped_instances = [inst for inst in worker_instances
                             if inst.status.lower() == 'terminated']

        for inst in started_instances:
            inst_meta = self.instances_db.get(inst.id)
            if not inst_meta or inst_meta.status == INSTANCE_STATUS_AVAILABLE:
                # Set the instance to used before starting the job in case
                # it calls back to /results very quickly before setting status.
                self.save_worker_instance(Box(id=inst.id,
                                              name=inst.name,
                                              inst=inst,
                                              status=INSTANCE_STATUS_USED,
                                              assigned_at=SERVER_TIMESTAMP))
                self.assign_job_to_instance(inst.id, job)

                log.success(f'Marked job {job.id} to start on '
                            f'running instance {inst.id}')
                break
        else:
            # No started instances available
            if stopped_instances:
                inst = stopped_instances[0]
                self.save_worker_instance(Box(id=inst.id,
                                              name=inst.name,
                                              inst=inst,
                                              status=INSTANCE_STATUS_USED,
                                              assigned_at=SERVER_TIMESTAMP,
                                              started_at=SERVER_TIMESTAMP,))
                self.assign_job_to_instance(inst.id, job)
                self.gce_ops_in_progress.append(self.start_instance(inst))
                log.success(
                    f'Started instance {inst.id} for job {job.id}')
            else:
                if len(worker_instances) >= MAX_WORKER_INSTANCES:
                    log.error(
                        f'Over instance limit, waiting for instances to become '
                        f'available to run job {job.id}')
                    return job
                create_op = self.create_instance(
                    current_instances=worker_instances)
                instance_id = create_op.targetId
                instance_name = create_op.targetLink.split('/')[-1]
                self.save_worker_instance(Box(id=instance_id,
                                              name=instance_name,
                                              status=INSTANCE_STATUS_USED,
                                              assigned_at=SERVER_TIMESTAMP,
                                              started_at=SERVER_TIMESTAMP,
                                              created_at=SERVER_TIMESTAMP))
                self.assign_job_to_instance(instance_id, job)
                self.gce_ops_in_progress.append(create_op)
                log.success(f'Created instance {instance_id} for '
                            f'job {job.id}')
        # TODO(Challenge): For network separation: Set DEEPDRIVE_SIM_HOST
        # TODO(Challenge): For network separation: Set network tags between bot and problem container for port 5557
        return job

    def get_worker_instances(self):
        return self.list_instances(WORKER_INSTANCE_LABEL)

    def confirm_evaluation(self, job) -> bool:
        if in_test():
            status = JOB_STATUS_CREATED
            ret = True
        elif dbox(job).confirmed:
            log.info(f'Job already confirmed '
                     f'{job.to_json(indent=2, default=str)}')
            status = JOB_STATUS_CREATED
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
                status = JOB_STATUS_CREATED
                log.success(f'Confirmed eval job '
                            f'{job.to_json(indent=2, default=str)} at {url}')
                ret = True
        job.status = status
        job.confirmed = ret
        self.save_job(job)
        return ret

    def start_instance(self, inst):
        return self.gce.instances().start(
            project=self.project,
            zone=self.zone,
            instance=inst.name).execute()

    def assign_job_to_instance(self, instance_id, job):
        # TODO: Compare and swap
        job.status = JOB_STATUS_ASSIGNED
        job.instance_id = instance_id
        job.started_at = SERVER_TIMESTAMP
        self.save_job(job)

    def save_worker_instance(self, worker_instance):
        self.instances_db.set(worker_instance.id, worker_instance)

    def save_job(self, job):
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
        # TODO: If job is CI, no GPU needed, but maybe more CPU
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

    def should_start_job(self, job) -> bool:
        if job.job_type == JOB_TYPE_EVAL:
            if not self.confirm_evaluation(job):
                ret = False
            else:
                problem = job.eval_spec.problem

                # Verify that the specified problem is supported
                if problem not in SUPPORTED_PROBLEMS:
                    log.error(f'Unsupported problem "{problem}"')
                    job.status = JOB_STATUS_DENIED_CONFIRMATION
                    self.save_job(job)
                    ret = False
                else:
                    ret = True
        elif job.job_type == JOB_TYPE_SIM_BUILD:
            ret = True
        else:
            log.error(f'Unsupported job type {job.job_type}, skipping job '
                      f'{job.to_json(indent=2, default=str)}')
            ret = False
        return ret

    def check_for_idle_instances(self):
        available_instances = self.instances_db.where(
            'status', '==', INSTANCE_STATUS_AVAILABLE)
        gce_workers = self.get_worker_instances()
        for instance in available_instances:
            last_dt = get_datetime_from_datetime_nanos(instance)
            idle_time = datetime.utcnow() - last_dt
            gce_worker = [w for w in gce_workers if w.id == dbox(instance).id]
            if gce_worker:
                gce_status = gce_worker[0].status
            else:
                gce_status = 'NOT FOUND'

            if idle_time > timedelta(minutes=5) and gce_status == 'RUNNING':
                log.info(f'Stopping idle instance {box2json(instance)}')
                stop_op = self.gce.instances().stop(
                    project=self.project,
                    zone=self.zone,
                    instance=instance.name).execute()
                return stop_op

    def prune_terminated_instances(self, worker_instances):
        worker_ids = [w.id for w in worker_instances]
        db_instances = self.instances_db.where('id', '>', '')
        term_db = get_db('deepdrive_worker_instances_terminated')
        for dbinst in db_instances:
            if dbinst.id not in worker_ids:
                term_db.set(dbinst.id, dbinst)
                self.instances_db.delete(dbinst.id)


def main():
    # compute = googleapiclient.discovery.build('compute', 'v1')
    # eval_instances = list_instances(compute, label='deepdrive-eval')
    mgr = JobManager()
    # worker_instances = mgr.list_instances(WORKER_INSTANCE_LABEL)
    # mgr.prune_terminated_instances(worker_instances)
    # mgr.check_for_idle_instances()  # Risky without owning semaphore!
    pass


if __name__ == '__main__':
    main()
