# import boot  # Must be first
from problem_constants import constants
from eval_manager import EvaluationManager
from singleton_loop import SingletonLoop


def main():
    eval_mgr = EvaluationManager()
    eval_mgr.check_for_finished_jobs()
    SingletonLoop(loop_name=constants.EVAL_LOOP_ID,
                  fn=eval_mgr.loop).run()


if __name__ == '__main__':
    main()
