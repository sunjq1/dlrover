# Copyright 2022 The DLRover Authors. All rights reserved.
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

import time
import unittest

from dlrover.proto import elastic_training_pb2
from dlrover.python.common.constants import NodeStatus, NodeType
from dlrover.python.master.elastic_training.elastic_ps import ElasticPsService
from dlrover.python.master.monitor.speed_monitor import SpeedMonitor
from dlrover.python.master.node.node_manager import create_node_manager
from dlrover.python.master.servicer import MasterServicer
from dlrover.python.master.shard.task_manager import TaskManager
from dlrover.python.master.stats.job_collector import JobMetricCollector
from dlrover.python.tests.test_utils import MockJobParams


class MasterServicerTest(unittest.TestCase):
    def setUp(self) -> None:
        params = MockJobParams()
        params.initilize()
        speed_monitor = SpeedMonitor()
        self.task_manager = TaskManager(False, speed_monitor)
        self.node_manager = create_node_manager(params, speed_monitor)
        self.job_metric_collector = JobMetricCollector(
            "1", "default", "local", "dlrover"
        )
        self.elastic_ps_service = ElasticPsService()
        self.servicer = MasterServicer(
            task_manager=self.task_manager,
            node_manager=self.node_manager,
            speed_monitor=speed_monitor,
            rendezvous_server=None,
            job_metric_collector=self.job_metric_collector,
            elastic_ps_service=self.elastic_ps_service,
        )

    def test_dataset_service(self):
        request = elastic_training_pb2.ReportDatasetShardParamsRequest()
        request.batch_size = 10
        request.num_epochs = 1
        request.dataset_size = 1000
        request.shuffle = False
        request.num_minibatches_per_shard = 10
        request.dataset_name = "test"
        request.task_type = elastic_training_pb2.TRAINING
        request.storage_type = "text"
        self.servicer.report_dataset_shard_params(request, None)

        collector = self.job_metric_collector._stats_collector
        self.assertEqual(collector._dataset_metric.get_size(), 1000)

        request = elastic_training_pb2.GetTaskRequest()
        request.worker_id = 0
        request.dataset_name = "test"
        task = self.servicer.get_task(request, None)
        self.assertEqual(task.task_id, 0)
        self.assertEqual(task.shard.start, 0)
        self.assertEqual(task.shard.end, 100)

        request = elastic_training_pb2.ReportTaskResultRequest()
        request.task_id = 0
        request.dataset_name = "test"
        self.servicer.report_task_result(request, None)
        self.assertEqual(len(self.task_manager._datasets["test"].doing), 0)

        request = elastic_training_pb2.DatasetMeta()
        request.dataset_name = "test"

        checkpoint = self.servicer.get_shard_checkpoint(request, None)
        self.assertLessEqual(10, len(checkpoint.content))
        self.servicer.report_shard_checkpoint(checkpoint, None)

        res = self.servicer.get_dataset_epoch(request, None)
        self.assertEqual(res.epoch, 1)

    def test_metric_service(self):
        self.node_manager._init_job_nodes()
        request = elastic_training_pb2.ReportUsedResourceRequest()
        request.memory = 4096
        request.cpu = 2
        request.node_id = 0
        request.node_type = NodeType.WORKER
        self.servicer.report_used_resource(request, None)
        request.node_type = NodeType.PS
        self.servicer.report_used_resource(request, None)

        request = elastic_training_pb2.ModelMetric()
        request.tensor_stats.variable_count = 100
        request.tensor_stats.total_variable_size = 10000

        request.op_stats.op_count = 100
        request.op_stats.flops = 10000
        self.servicer.report_model_metric(request, None)
        collector = self.job_metric_collector._stats_collector
        self.assertEqual(collector._model_metric.op_stats.flops, 10000)

        worker0 = self.node_manager._job_nodes[NodeType.WORKER][0]
        worker0.status = NodeStatus.RUNNING
        ps0 = self.node_manager._job_nodes[NodeType.PS][0]
        ps0.status = NodeStatus.RUNNING
        request = elastic_training_pb2.GlobalStepRecord()
        self.task_manager._speed_monitor.add_running_worker(NodeType.WORKER, 0)
        ts = int(time.time())
        request.timestamp = ts
        request.global_step = 100
        self.servicer.report_global_step(request, None)
        request.timestamp = ts + 10
        request.global_step = 1100
        self.servicer.report_global_step(request, None)
        self.job_metric_collector._report_runtime_stats()
        self.assertEqual(len(collector._runtime_stats), 2)
        self.assertEqual(collector._runtime_stats[0].global_step, 1100)
        self.assertEqual(len(collector._runtime_stats[0].running_nodes), 4)

        request.timestamp = ts + 20
        request.global_step = 2100
        self.servicer.report_global_step(request, None)

        request.timestamp = ts + 30
        request.global_step = 3100
        self.servicer.report_global_step(request, None)

        request.timestamp = ts + 40
        request.global_step = 4100
        self.servicer.report_global_step(request, None)

        request.timestamp = ts + 50
        request.global_step = 5100
        self.servicer.report_global_step(request, None)
        self.assertTrue(self.servicer._start_autoscale)