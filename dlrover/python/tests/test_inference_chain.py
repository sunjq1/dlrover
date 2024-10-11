# Copyright 2024 The DLRover Authors. All rights reserved.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import unittest
from unittest.mock import patch

from dlrover.python.common import env_utils
from dlrover.python.common.constants import NodeEnv, NodeType
from dlrover.python.diagnosis.common.constants import (
    EnvConfigKey,
    InferenceConfigKey,
)
from dlrover.python.diagnosis.common.inference_chain import (
    Inference,
    InferenceAttribute,
    InferenceDescription,
    InferenceName,
    is_same_inference,
)
from dlrover.python.diagnosis.inferencechain.inference_chain import (
    InferenceChain,
)
from dlrover.python.diagnosis.inferencechain.inferenceoperator.check_failure_node_operator import (  # noqa: E501
    CheckFailureNodeOperator,
)
from dlrover.python.diagnosis.inferencechain.inferenceoperator.check_training_hang_operator import (  # noqa: E501
    CheckTrainingHangOperator,
)
from dlrover.python.diagnosis.inferencechain.inferenceoperator.collect_metrics_operator import (  # noqa: E501
    CollectMetricsOperator,
)
from dlrover.python.elastic_agent.master_client import (
    MasterClient,
    build_master_client,
)
from dlrover.python.tests.test_utils import start_local_master


class InferenceChainTest(unittest.TestCase):
    def setUp(self):
        self.master_proc, self.addr = start_local_master()
        MasterClient._instance = build_master_client(self.addr, 1)

    def tearDown(self):
        pass

    def test_check_training_hang_operator(self):
        operator = CheckTrainingHangOperator(None)
        inf = Inference(
            name=InferenceName.TRAINING,
            attribution=InferenceAttribute.ISORNOT,
            description=InferenceDescription.HANG,
        )
        self.assertTrue(operator.is_compatible(inf))

        results = operator.infer([inf])
        self.assertEqual(
            results[0],
            Inference(
                name=InferenceName.TRAINING,
                attribution=InferenceAttribute.NOT,
                description=InferenceDescription.HANG,
            ),
        )

    def test_check_failure_node_operator(self):
        file = "data/training.log"
        path = os.path.dirname(__file__)
        file_path = os.path.join(path, file)

        operator = CheckFailureNodeOperator()
        inf = Inference(
            name=InferenceName.NODE,
            attribution=InferenceAttribute.ISORNOT,
            description=InferenceDescription.FAILURE,
            configs={
                InferenceConfigKey.LOG_FILE: file_path,
                InferenceConfigKey.ERRORS: "error code is 507035",
            },
        )
        self.assertTrue(operator.is_compatible(inf))

        results = operator.infer([inf])
        failure_inf = Inference(
            name=InferenceName.NODE,
            attribution=InferenceAttribute.IS,
            description=InferenceDescription.FAILURE,
        )
        self.assertTrue(is_same_inference(results[0], failure_inf))

        #########################################################
        inf = Inference(
            name=InferenceName.NODE,
            attribution=InferenceAttribute.ISORNOT,
            description=InferenceDescription.FAILURE,
            configs={
                InferenceConfigKey.LOG_FILE: file_path,
                InferenceConfigKey.ERRORS: "error code is 123456",
            },
        )

        results = operator.infer([inf])
        not_failure_inf = Inference(
            name=InferenceName.NODE,
            attribution=InferenceAttribute.NOT,
            description=InferenceDescription.FAILURE,
        )
        self.assertTrue(is_same_inference(results[0], not_failure_inf))

    def test_inference_chain(self):
        file = "data/training.log"
        path = os.path.dirname(__file__)
        file_path = os.path.join(path, file)
        inf = Inference(
            name=InferenceName.NODE,
            attribution=InferenceAttribute.ISORNOT,
            description=InferenceDescription.FAILURE,
            configs={
                InferenceConfigKey.LOG_FILE: file_path,
                InferenceConfigKey.ERRORS: "error code is 507035",
            },
        )

        operators = [CheckFailureNodeOperator()]
        ic = InferenceChain([inf], operators)
        results = ic.infer()
        failure_inf = Inference(
            name=InferenceName.NODE,
            attribution=InferenceAttribute.IS,
            description=InferenceDescription.FAILURE,
        )
        self.assertTrue(is_same_inference(results[0], failure_inf))

    @patch(
        "dlrover.python.diagnosis.datacollector.xpu_timer_metric_collector"
        ".XpuTimerMetricsCollector.collect_data"
    )
    def test_collect_metrics_operator(self, mock_collector):
        mock_collector.return_value = "data"
        operator = CollectMetricsOperator()
        inf = Inference(
            name=InferenceName.WORKER,
            attribution=InferenceAttribute.COLLECT,
            description=InferenceDescription.METRICS,
        )
        self.assertTrue(operator.is_compatible(inf))

        env_utils.set_env(EnvConfigKey.XPU_TIMER_PORT, 18889)
        env_utils.set_env(NodeEnv.NODE_ID, 1)
        env_utils.set_env(NodeEnv.NODE_TYPE, NodeType.WORKER)
        env_utils.set_env(NodeEnv.NODE_RANK, 1)
        infs = operator.infer([])
        self.assertEqual(len(infs), 0)


if __name__ == "__main__":
    unittest.main()
