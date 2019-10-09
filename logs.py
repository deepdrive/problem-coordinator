from loguru import logger as log
from botleague_helpers.logs import add_slack_error_sink, add_stackdriver_sink

LOG_NAME = 'deepdrive-coordinator'

add_stackdriver_sink(log, LOG_NAME)
add_slack_error_sink(log, '#deepdrive-alerts', log_name='Problem Coordinator')
