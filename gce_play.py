#!/usr/bin/env python

# Copyright 2015 Google Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Example of using the Compute Engine API to create and delete instances.

Creates a new compute engine instance and uses it to apply a caption to
an image.

    https://cloud.google.com/compute/docs/tutorials/python-guide

For more information, see the README.md under /compute.
"""

import argparse
import os
import time
from os import path

import googleapiclient.discovery
from box import Box
from six.moves import input


# [START list_instances]
import constants
from utils import read_json

ROOT = os.path.dirname(os.path.realpath(__file__))


def list_instances(compute, project, zone):
    result = compute.instances().list(project=project, zone=zone).execute()
    return result['items'] if 'items' in result else None
# [END list_instances]


def create_instance(compute, project, zone, instance_name):
    config = Box.from_json(
        filename=path.join(ROOT, 'cloud_configs/eval_instance_create.json'))

    config.name = instance_name
    config.disks[0].deviceName = instance_name

    return compute.instances().insert(
        project=project,
        zone=zone,
        body=config.to_dict()).execute()
# [END create_instance]


# [START delete_instance]
def delete_instance(compute, project, zone, name):
    return compute.instances().delete(
        project=project,
        zone=zone,
        instance=name).execute()
# [END delete_instance]


# [START wait_for_operation]
def wait_for_operation(compute, project, zone, operation):
    print('Waiting for operation to finish...')
    while True:
        result = compute.zoneOperations().get(
            project=project,
            zone=zone,
            operation=operation).execute()

        if result['status'] == 'DONE':
            print("done.")
            if 'error' in result:
                raise Exception(result['error'])
            return result

        time.sleep(1)
# [END wait_for_operation]


# [START run]
def main_old(project, bucket, zone, instance_name, wait=True):
    compute = googleapiclient.discovery.build('compute', 'v1')

    print('Creating instance.')

    operation = create_instance(compute, project, zone, instance_name, bucket)
    wait_for_operation(compute, project, zone, operation['name'])

    instances = list_instances(compute, project, zone)

    print('Instances in project %s and zone %s:' % (project, zone))
    for instance in instances:
        print(' - ' + instance['name'])

    print("""
Instance created.
It will take a minute or two for the instance to complete work.
Check this URL: http://storage.googleapis.com/{}/output.png
Once the image is uploaded press enter to delete the instance.
""".format(bucket))

    if wait:
        input()

    print('Deleting instance.')

    operation = delete_instance(compute, project, zone, instance_name)
    wait_for_operation(compute, project, zone, operation['name'])


def main():
    compute = googleapiclient.discovery.build('compute', 'v1')
    project = constants.GCP_PROJECT
    zone = constants.GCP_ZONE
    instances = compute.instances().list(project=project,
                                         zone=zone).execute()
    # operation = create_instance(compute=compute,
    #                             project=project, zone=zone,
    #                             instance_name='deepdrive-eval-test')
    # print(operation)

    pass
    # wait_for_operation()



if __name__ == '__main__':
    main()


    # operation = create_instance(compute, project, zone, instance_name, bucket)



    # parser = argparse.ArgumentParser(
    #     description=__doc__,
    #     formatter_class=argparse.RawDescriptionHelpFormatter)
    # parser.add_argument('project_id', help='Your Google Cloud project ID.')
    # parser.add_argument(
    #     'bucket_name', help='Your Google Cloud Storage bucket name.')
    # # parser.add_argument(
    # #     '--zone',
    # #     default='us-central1-f',
    # #     help='Compute Engine zone to deploy to.')
    # parser.add_argument(
    #     '--name', default='demo-instance', help='New instance name.')
    #
    # args = parser.parse_args()
    #
    # main(args.project_id, args.bucket_name, args.zone, args.name)
# [END run]
