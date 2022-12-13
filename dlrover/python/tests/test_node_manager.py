# Copyright 2022 The EasyDL Authors. All rights reserved.
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

import unittest
from unittest import mock

from dlrover.python.common.constants import (
    JobExitReason,
    NodeEventType,
    NodeExitReason,
    NodeStatus,
    NodeType,
)
from dlrover.python.common.node import NodeGroupResource, NodeResource
from dlrover.python.master.master import Master
from dlrover.python.master.monitor.speed_monitor import SpeedMonitor
from dlrover.python.master.node.event_callback import (
    ClusterContext,
    TaskRescheduleCallback,
    TFPSNodeHandlingCallback,
)
from dlrover.python.master.node.node_manager import create_node_manager
from dlrover.python.master.node.status_flow import (
    NODE_STATE_FLOWS,
    NodeStateFlow,
    get_node_state_flow,
)
from dlrover.python.master.node.training_node import (
    get_critical_worker_index,
    set_critical_node,
)
from dlrover.python.master.resource.job import JobResource
from dlrover.python.master.resource.optimizer import ResourcePlan
from dlrover.python.master.watcher.base_watcher import Node, NodeEvent
from dlrover.python.tests.test_utils import (
    MockJobParams,
    create_task_manager,
    mock_k8s_client,
)

_MOCK_JOB_UUID = "11111"


def get_service_fn(*args):
    return "test:2222"


def get_job_uuid():
    return "11111"


def _get_node_name(type, id):
    return "{}-{}".format(type, id)


class NodeStatusFlowTest(unittest.TestCase):
    def test_get_node_state_flow(self):
        flow: NodeStateFlow = get_node_state_flow(
            NodeStatus.PENDING, NodeEventType.MODIFIED, NodeStatus.RUNNING
        )
        self.assertEqual(flow, NODE_STATE_FLOWS[2])

        flow = get_node_state_flow(
            NodeStatus.RUNNING, NodeEventType.MODIFIED, NodeStatus.SUCCEEDED
        )
        self.assertEqual(flow, NODE_STATE_FLOWS[5])

        flow = get_node_state_flow(
            NodeStatus.RUNNING, NodeEventType.DELETED, NodeStatus.DELETED
        )
        self.assertEqual(flow, NODE_STATE_FLOWS[8])
        self.assertTrue(flow.should_relaunch)

        flow = get_node_state_flow(
            NodeStatus.SUCCEEDED, NodeEventType.DELETED, NodeStatus.DELETED
        )
        self.assertEqual(flow, NODE_STATE_FLOWS[-2])
        self.assertFalse(flow.should_relaunch)


class JobConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        mock_k8s_client()

    def test_job_resource(self):
        job = JobResource()
        job.add_node_group_resource(NodeType.PS, 3, "cpu=1,memory=4096Mi", "")
        job.add_node_group_resource(
            NodeType.WORKER, 5, "cpu=1,memory=4096Mi", ""
        )
        group_resource = job.get_node_group_resource(NodeType.WORKER)
        self.assertEqual(group_resource.count, 5)
        self.assertEqual(group_resource.node_resource.cpu, 1)
        self.assertEqual(group_resource.node_resource.memory, 4096)
        group_resource = job.get_node_group_resource(NodeType.PS)
        self.assertEqual(group_resource.count, 3)
        self.assertEqual(group_resource.node_resource.cpu, 1)
        self.assertEqual(group_resource.node_resource.memory, 4096)
        node_types = job.get_node_types()
        self.assertListEqual(node_types, [NodeType.PS, NodeType.WORKER])
        self.assertEqual(job.worker_num, 5)
        self.assertEqual(job.ps_num, 3)

        nodes = job.init_job_node_meta(1, get_service_fn, _get_node_name)
        self.assertEqual(len(nodes[NodeType.WORKER]), 5)
        self.assertEqual(len(nodes[NodeType.PS]), 3)
        self.assertEqual(nodes[NodeType.PS][0].id, 0)
        self.assertEqual(nodes[NodeType.PS][0].type, NodeType.PS)
        self.assertEqual(nodes[NodeType.WORKER][2].id, 2)
        self.assertEqual(nodes[NodeType.WORKER][0].type, NodeType.WORKER)
        self.assertEqual(nodes[NodeType.WORKER][0].config_resource.cpu, 1)
        self.assertEqual(
            nodes[NodeType.WORKER][0].config_resource.memory, 4096
        )

    def test_set_critical_node(self):
        job = JobResource()
        job.add_node_group_resource(NodeType.PS, 3, "cpu=1,memory=4096Mi", "")
        job.add_node_group_resource(
            NodeType.WORKER, 5, "cpu=1,memory=4096Mi", ""
        )

        nodes = job.init_job_node_meta(1, get_service_fn, _get_node_name)
        set_critical_node(
            nodes, critical_worker_index={0: 3}, ps_relaunch_max_num=2
        )
        self.assertTrue(nodes[NodeType.PS][0].critical)
        self.assertEqual(nodes[NodeType.PS][0].max_relaunch_count, 2)
        self.assertTrue(nodes[NodeType.WORKER][0].critical)
        self.assertEqual(nodes[NodeType.WORKER][0].max_relaunch_count, 3)
        self.assertTrue(nodes[NodeType.WORKER][0].critical)
        self.assertFalse(nodes[NodeType.WORKER][1].critical)

    def test_get_critical_worker_index(self):
        params = MockJobParams()
        params.initilize()
        critical_worker = get_critical_worker_index(params)
        self.assertDictEqual(critical_worker, {0: 3})
        params.node_params[NodeType.WORKER].critical_nodes = "0:1"
        critical_worker = get_critical_worker_index(params)
        self.assertDictEqual(critical_worker, {0: 1})
        params.node_params[NodeType.WORKER].critical_nodes = "all"
        critical_worker = get_critical_worker_index(params)
        self.assertDictEqual(critical_worker, {0: 3, 1: 3, 2: 3})

    def test_create_node_manager(self):
        params = MockJobParams()
        params.initilize()
        manager = create_node_manager(params, SpeedMonitor())
        self.assertEqual(manager._ps_relaunch_max_num, 1)
        manager.start()
        self.assertEqual(manager._job_uuid, _MOCK_JOB_UUID)
        self.assertEqual(len(manager._job_nodes), 4)
        self.assertTrue(manager._job_nodes[NodeType.PS][0].critical)

        node = Node(
            node_type=NodeType.WORKER,
            node_id=1,
            status=NodeStatus.RUNNING,
            config_resource=NodeResource(1, 4096),
        )

        manager.update_node_resource_usage(NodeType.WORKER, 0, 0.7, 2048)
        self.assertEqual(
            manager._job_nodes[NodeType.WORKER][0].used_resource.cpu, 0.7
        )
        self.assertEqual(
            manager._job_nodes[NodeType.WORKER][0].used_resource.memory, 2048
        )

        node_event: NodeEvent = NodeEvent(NodeEventType.MODIFIED, node)
        manager._process_event(node_event)
        self.assertEqual(
            manager._job_nodes[NodeType.WORKER][1].status, NodeStatus.RUNNING
        )

        should_relaunch = manager._should_relaunch(node, NODE_STATE_FLOWS[5])
        self.assertFalse(should_relaunch)

        should_relaunch = manager._should_relaunch(node, NODE_STATE_FLOWS[6])
        self.assertTrue(should_relaunch)

        node.relaunch_count = node.max_relaunch_count + 1
        should_relaunch = manager._should_relaunch(node, NODE_STATE_FLOWS[6])
        self.assertFalse(should_relaunch)

        node.exit_reason = NodeExitReason.FATAL_ERROR
        should_relaunch = manager._should_relaunch(node, NODE_STATE_FLOWS[6])
        self.assertFalse(should_relaunch)

    def test_recover_tasks_for_failed_workers(self):
        dataset_name = "test"
        task_manager = create_task_manager()
        task_callback = TaskRescheduleCallback(task_manager)
        params = MockJobParams()
        params.initilize()
        manager = create_node_manager(params, SpeedMonitor())
        manager._init_job_nodes()
        manager.add_node_event_callback(task_callback)

        dataset = task_manager.get_dataset(dataset_name)
        task_manager.get_dataset_task(NodeType.WORKER, 0, dataset_name)
        node = Node(
            node_type=NodeType.WORKER,
            node_id=0,
            status=NodeStatus.RUNNING,
            config_resource=NodeResource(1, 4096),
        )
        manager._process_node_events(NODE_STATE_FLOWS[7], node)
        self.assertEqual(len(dataset.doing), 0)

    def test_create_initial_nodes(self):
        params = MockJobParams()
        params.initilize()
        manager = create_node_manager(params, SpeedMonitor())
        manager._init_job_nodes()
        plan = manager._create_initial_scale_plan()
        self.assertEqual(
            plan.ps_addrs,
            [
                "test-edljob-ps-0.default.svc:2222",
                "test-edljob-ps-1.default.svc:2222",
                "test-edljob-ps-2.default.svc:2222",
            ],
        )
        self.assertEqual(plan.node_group_resources[NodeType.PS].count, 3)
        self.assertEqual(plan.node_group_resources[NodeType.WORKER].count, 3)

    def test_check_worker_status(self):
        params = MockJobParams()
        params.initilize()
        manager = create_node_manager(params, SpeedMonitor())
        manager._init_job_nodes()
        self.assertFalse(manager.all_workers_exited())

        for worker in manager._job_nodes[NodeType.WORKER].values():
            worker.status = NodeStatus.FINISHED
        for worker in manager._job_nodes[NodeType.CHIEF].values():
            worker.status = NodeStatus.FINISHED
        for worker in manager._job_nodes[NodeType.EVALUATOR].values():
            worker.status = NodeStatus.FINISHED
        self.assertTrue(manager.all_workers_exited())

        for worker in manager._job_nodes[NodeType.WORKER].values():
            worker.status = NodeStatus.FAILED
        for worker in manager._job_nodes[NodeType.CHIEF].values():
            worker.status = NodeStatus.FAILED
        for worker in manager._job_nodes[NodeType.EVALUATOR].values():
            worker.status = NodeStatus.FAILED
        self.assertTrue(manager.all_workers_failed())

        for worker in manager._job_nodes[NodeType.PS].values():
            worker.status = NodeStatus.FINISHED
        manager._job_nodes[NodeType.WORKER][0].status = NodeStatus.RUNNING
        self.assertFalse(manager.all_critical_node_completed())
        manager._job_nodes[NodeType.WORKER][0].status = NodeStatus.FINISHED
        self.assertTrue(manager.all_critical_node_completed())

    def test_tf_ps_node_handling(self):
        params = MockJobParams()
        params.initilize()
        master = Master(2222, params)
        master.node_manager._init_job_nodes()
        master.node_manager._scaler.scale = mock.MagicMock(return_value=True)
        callback = TFPSNodeHandlingCallback(master)

        node = Node(NodeType.PS, 0, None)
        node.exit_reason = NodeExitReason.OOM
        reason = callback.get_job_exit_reason(node)
        self.assertEqual(reason, JobExitReason.PS_OOM_ERROR)

        node.type = NodeType.WORKER
        reason = callback.get_job_exit_reason(node)
        self.assertEqual(reason, JobExitReason.CODE_ERROR)

        master.speed_monitor.add_running_worker(NodeType.WORKER, 0)
        master.speed_monitor.add_running_worker(NodeType.WORKER, 1)
        cluster_context = ClusterContext(master.node_manager)
        master.speed_monitor.set_target_worker_num(2)
        node.exit_reason = NodeExitReason.FATAL_ERROR
        callback.on_node_failed(node, cluster_context)
        self.assertEqual(master.speed_monitor._target_worker_num, 1)
        self.assertEqual(len(master.speed_monitor.running_workers), 1)

    def test_execute_job_optimization_plan(self):
        params = MockJobParams()
        params.initilize()
        manager = create_node_manager(params, SpeedMonitor())
        manager._init_job_nodes()

        manager._scaler.scale = mock.MagicMock(return_value=True)
        plan = ResourcePlan()
        plan.node_group_resources[NodeType.WORKER] = NodeGroupResource(
            6, NodeResource(4, 4096)
        )
        plan.node_resources["test-edljob-worker-0"] = NodeResource(8, 8192)
        plan.node_resources["test-edljob-ps-1"] = NodeResource(8, 8192)
        manager._ps_manager._nodes[1].status = NodeStatus.RUNNING
        scale_plan = manager._execute_job_optimization_plan(plan)
        self.assertEqual(len(manager._ps_manager._nodes), 4)
        self.assertEqual(len(manager._worker_manager._nodes), 7)
        self.assertEqual(scale_plan.node_group_resources[NodeType.PS].count, 3)
        self.assertEqual(
            scale_plan.node_group_resources[NodeType.WORKER].count, 6
        )
        self.assertEqual(len(scale_plan.remove_nodes), 1)
        self.assertEqual(len(scale_plan.launch_nodes), 2)

        ps_addrs = []
        for i in range(3):
            ps_addrs.append("test-edljob-ps-{}.default.svc:2222".format(i))

        self.assertListEqual(scale_plan.ps_addrs, ps_addrs)