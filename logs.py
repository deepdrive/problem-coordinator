from loguru import logger as log
from botleague_helpers.logs import add_slack_error_sink, add_stackdriver_sink

from common import STACKDRIVER_LOG_NAME

add_stackdriver_sink(log, STACKDRIVER_LOG_NAME)
add_slack_error_sink(log, '#deepdrive-alerts')
