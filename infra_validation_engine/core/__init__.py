# coding: utf-8
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import json
import traceback
from abc import ABCMeta, abstractmethod
import logging
from infra_validation_engine.core.exceptions import DirectoryNotFoundError, PreConditionNotSatisfiedError


class Pool:
    """
    Manages a list of all available infra tests and the stages
    """
    tests = []
    stages = []
    logger = logging.getLogger(__name__)

    @staticmethod
    def register_test(name):
        Pool.tests.append(name)
        Pool.logger.debug("Registered {test} in test pool".format(test=name))

    @staticmethod
    def register_stage(name):
        Pool.stages.append(name)
        Pool.logger.debug("Registered {stage} in test pool".format(stage=name))

    @staticmethod
    def get_all_tests():
        return Pool.tests

    @staticmethod
    def get_all_stages():
        return Pool.stages


class InfraTest:
    __metaclass__ = ABCMeta

    def __init__(self, name, description, host, fqdn):
        self.host = host
        self.name = name
        self.description = description
        self.fqdn = fqdn
        self.rc = -1
        self.err = None
        self.out = None
        # If message is specified, it will be present in report and output in api/standalone modes
        self.message = None
        self.warn = False
        self.logger = logging.getLogger(__name__)
        self.report = self.report = {'name': self.name, 'description': self.description, 'result': 'fail'}
        self.exit_code = 0

    @abstractmethod
    def run(self):
        pass

    @abstractmethod
    def fail(self):
        pass

    def resolution(self):
        return ""

    def execute(self):
        log_str = "{test} on {fqdn}:".format(fqdn=self.fqdn, test=self.name)
        self.logger.info("{log_str} running".format(log_str=log_str))
        try:
            self.logger.info("{log_str} passed!".format(log_str=log_str))
            if self.run():  # test_passed
                # handle warnings
                if self.warn:
                    self.exit_code = 3
                    self.logger.warning("{log_str} {message}".format(log_str=log_str, message=self.message))
                self.report['result'] = 'pass'
            else:  # test failed
                try:
                    self.exit_code = 1
                    self.fail()
                except Exception as ex:
                    self.logger.error("{test} failed on {fqdn}! {details}".format(test=self.name,
                                                                                  fqdn=self.fqdn, details=ex.message))
                    self.logger.info("{log_str} {error} occurred!!".format(log_str=log_str, error=type(ex)),
                                     exc_info=True)
                    self.report["error"] = ex.message
                    self.report["trace"] = traceback.format_exc()
        except Exception as ex:
            self.exit_code = 1
            self.logger.error("{log_str} Could not run {test}!".format(log_str=log_str, test=self.name))
            self.logger.info(
                "{log_str} {error} occurred for {test}".format(log_str=log_str, test=self.name, error=type(ex)),
                exc_info=True)
            self.report["result"] = "exec_fail"
            self.report["error"] = ex.message
            self.report["trace"] = traceback.format_exc()
        if self.message is not None:
            self.logger.info(self.message)
            self.report['message'] = self.message

        # self.report['exit_code'] = self.exit_code


class Executor:
    """
    An abstract executor for InfraTests
    """
    __metaclass__ = ABCMeta

    def __init__(self, name):
        self.name = name
        self.logger = logging.getLogger(__name__)
        self.report = {"executor_name": self.name}
        self.infra_tests = []
        self.exit_code = 0
        self.hard_error_pre_condition = True

    def register_infra_test(self, infra_test):
        """ Register a test to be run by this executor """
        self.infra_tests.append(infra_test)

    @abstractmethod
    def pre_condition(self):
        """ Gather Info needed before running tests. Fail if the info is not available """
        pass

    def run(self):
        """ Run the Tests """
        self.logger.info("Execution infrastructure tests for {name}".format(name=self.name))
        test_name_csv = ', '.join([test.name for test in self.infra_tests])
        self.logger.info("Executor {name} has the following tests registered: {test_name_csv}".format(
            name=self.name,
            test_name_csv=test_name_csv))

    def post_process(self):
        """ Generate Report and update Exit Code """
        test_reports = [test.report for test in self.infra_tests]
        self.report['reports'] = test_reports
        self.logger.api(json.dumps(self.report, indent=4))
        exit_codes = [test.exit_code for test in self.infra_tests]
        if 1 in exit_codes:
            self.exit_code = 1
            self.report["result"] = "fail"
        elif 3 in exit_codes:
            self.exit_code = 3
            self.report["result"] = "warning"
        else:
            self.report["result"] = "pass"

    def execute(self):
        try:
            self.pre_condition()
        except PreConditionNotSatisfiedError as err:
            if self.hard_error_pre_condition:
                self.logger.error("The pre condition check for Executor {name} was not satisfied. "
                                  "Therefore, the execution of the following tests is being skipped: {tests}".format(
                                    name=self.name,
                                    tests=', '.join(
                                        ["{test} on {fqdn}".format(test=x.name, fqdn=x.fqdn) for x in self.infra_tests]
                                    )))
                self.logger.info("Exception info: {error}".format(error=err.message), exc_info=True)
                self.report["result"] = "exec_fail"
                self.report["error"] = err.message
                self.report["trace"] = traceback.format_exc()
                return
        # Ready to run tests
        self.run()
        # Update report
        self.post_process()


class Stage:
    """ Collection and Execution of InfraTests """

    __metaclass__ = ABCMeta

    def __init__(self, name, config_master_host, lightweight_component_hosts):
        self.name = name
        self.infra_tests = list()
        self.config_master_host = config_master_host
        self.lightweight_component_hosts = lightweight_component_hosts
        self.logger = logging.getLogger(__name__)
        self.register_tests()

    @abstractmethod
    def register_tests(self):
        pass

    def execute(self):
        """
        exit_code = 0 # all pass
        exit_code = 1 # some passed and some failed, or all tests failed
        exit_code = 3 # some passed and some tests raised warning, or all tests raised warning
        exit_code = 4 # some passed and some failed and some raised warning
        """
        self.logger.info("Execution infrastructure tests for {stage}".format(stage=self.name))
        test_name_csv = ', '.join([test.name for test in self.infra_tests])
        self.logger.info("Stage {stage} has the following tests registered: {test_name_csv}".format(stage=self.name,
                                                                                                    test_name_csv=test_name_csv))
        exit_code = 0
        reports = []
        for test in self.infra_tests:
            self.logger.info("Running {test_name} on {node}".format(test_name=test.name, node=test.fqdn))
            report = {'name': test.name, 'description': test.description, 'result': 'fail'}
            try:
                if test.run():  # test_passed
                    self.logger.info("{test} passed!".format(test=test.name))
                    # handle warnings
                    if test.warn:
                        exit_code = 3
                        self.logger.warning(test.message)
                    report['result'] = 'pass'
                else:  # test failed
                    try:
                        exit_code = 1
                        test.fail()
                    except Exception as ex:
                        self.logger.error("{test} failed! {details}".format(test=test.name, details=ex.message))
                        self.logger.info("{error} occurred for {test}".format(test=test.name, error=type(ex)),
                                         exc_info=True)
                        report["error"] = ex.message
                        report["trace"] = traceback.format_exc()
            except Exception as ex:
                exit_code = 1
                self.logger.error("Could not run {test}!".format(test=test.name))
                self.logger.info("{error} occurred for {test}".format(test=test.name, error=type(ex)), exc_info=True)
                report["result"] = "exec_fail"
                report["error"] = ex.message
                report["trace"] = traceback.format_exc()
            if test.message is not None:
                self.logger.info(test.message)
                report['message'] = test.message
            reports.append(report)
        self.logger.api(json.dumps(reports, indent=4))
        return exit_code


class StageType(ABCMeta):
    """
    Automatically register a class that has __metaclass__ = StageType in the Pool
    see: https://stackoverflow.com/a/100146
    """
    logger = logging.getLogger(__name__)

    def __init__(cls, name, bases, attrs):
        super(StageType, cls).__init__(name, bases, attrs)
        Pool.register_stage(name)
        StageType.logger.debug("Registering Stage {name}".format(name=name))


class InfraTestType(ABCMeta):
    """
    Automatically register a class with __metaclass__ = TestType in the Pool
    see: https://stackoverflow.com/a/100146
    """
    logger = logging.getLogger(__name__)

    def __init__(cls, name, bases, attrs):
        super(InfraTestType, cls).__init__(name, bases, attrs)
        Pool.register_test(name)
        StageType.logger.debug("Registering Test {name}".format(name=name))
