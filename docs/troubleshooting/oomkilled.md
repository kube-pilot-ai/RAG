---
title: "Troubleshooting Kubernetes pods: OOMKilled"
document_id: pod-oomkilled
resource: Pod
symptoms: [OOMKilled, OutOfMemory, CrashLoopBackOff, "exit code 137"]
reviewed_on: "2026-11-06"
---

# Troubleshooting Kubernetes pods: OOMKilled

## Meaning and symptoms

`OOMKilled` indicates a container termination associated with an out-of-memory
kill. Repeated restarts can subsequently appear as `CrashLoopBackOff`, which
alone does not establish an OOM cause. Exit code `137` means a SIGKILL-related
termination and is not sufficient evidence by itself. Distinguish container
memory-limit exhaustion from node-wide memory exhaustion using termination
details and node evidence. See [OOM investigation guidance](https://docs.cloud.google.com/kubernetes-engine/docs/troubleshooting/oom-events).

## Confirm the affected container

Record namespace, pod, container, restart count, termination time, node, and
recent workload changes. Replace example names; use namespace `default` when
none was provided.

```powershell
kubectl describe pod example-pod -n default
kubectl get pod example-pod -n default -o yaml
kubectl logs example-pod -n default -c app --previous --tail=100
kubectl get events -n default --field-selector involvedObject.kind=Pod,involvedObject.name=example-pod --sort-by=.metadata.creationTimestamp
```

Inspect both regular and init container statuses. Check `state.terminated` if
the container is stopped and `lastState.terminated` if it has restarted. A
typical illustrative record is:

```yaml
lastState:
  terminated:
    reason: OOMKilled
    exitCode: 137
restartCount: 3
```

Previous logs may be unavailable after pod replacement or log removal. An
abrupt kill may leave no application error. Do not infer the absence of an OOM
from empty logs or missing events; termination state is important evidence.

## Inspect limits and memory behavior

Read the affected container's actual resource settings, including defaults
applied to the pod. A request guides scheduling; it is not a memory cap. The
memory limit is enforced through kernel mechanisms and can lead to OOM kills.
Changing only the request will not raise an existing limit. Memory-backed
`emptyDir` storage can also consume the container's memory budget.
See [Kubernetes resource management](https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/).

```powershell
kubectl top pod example-pod -n default --containers
kubectl top node
```

`kubectl top` requires a working resource metrics API. A current sample after
restart does not show the peak before the kill. Compare historical usage with
the effective memory limit and the termination timestamp where monitoring is
available.

| Observed pattern | Possible explanation | Investigation |
| --- | --- | --- |
| Usage rises continuously | Leak or unbounded cache | Review profiles, cache bounds, and recent code changes |
| Kill during startup | Initialization peak or runtime heap settings | Examine startup work and container-aware runtime configuration |
| Kill during traffic/batch spikes | Excess concurrency or large in-memory payloads | Correlate workload size, queue depth, and parallelism |
| Stable usage close to the limit | Limit below measured workload needs | Evaluate a measured increase and available capacity |

These patterns require measurement; they are not confirmed causes from pod
status alone. A higher limit can provide temporary headroom without fixing a
leak.

## Check node pressure separately

```powershell
kubectl describe node example-node
kubectl get events --all-namespaces --field-selector involvedObject.kind=Node,involvedObject.name=example-node --sort-by=.metadata.creationTimestamp
```

Look for `MemoryPressure`, other affected workloads, and eviction messages.
`Evicted` is a pod eviction outcome and is different from a container's
`OOMKilled` termination. Node pressure can require capacity or workload
placement changes; increasing every container limit may worsen it. A healthy
node now does not rule out an earlier incident. See [node-pressure eviction](https://kubernetes.io/docs/concepts/scheduling-eviction/node-pressure-eviction/).

## Remediate and verify

Fix leaks, bound caches, reduce concurrency, or process data in smaller batches
when evidence supports those causes. If measured demand justifies more memory,
update the owning workload's resource configuration after checking node capacity
and namespace policies. Example values are illustrative, not sizing advice:

```yaml
resources:
  requests:
    memory: "256Mi"
  limits:
    memory: "512Mi"
```

Update the workload manifest and roll it out through your normal deployment
process. Confirm new pods receive the intended settings. Observe readiness,
restart counts, and memory under representative load, including the startup or
batch phase that previously failed. Old termination records can remain after
recovery; check whether new kills occur. See [memory configuration and OOM examples](https://kubernetes.io/docs/tasks/configure-pod-container/assign-memory-resource/).

## Using the current assistant

Start with `list_pods(namespace)` to identify the affected container and node.
Use `get_pod_details(pod_name, namespace)` to inspect `containers`,
`init_containers`, and `ephemeral_containers`. Each entry provides `state`,
`last_state`, `restarts`, images, and `resources.requests`/`resources.limits`.
If present, `allocated_resources` and `reported_resources` are also returned;
these can help distinguish requested settings from runtime-reported settings.
A missing status or field is unknown, not evidence of success or zero usage.

Confirm a recorded OOM termination from `state.terminated.reason` or
`last_state.terminated.reason` equal to `OOMKilled`. Report the associated
`exit_code`, timestamps, container name, and configured memory limit. A recorded
OOM kill establishes the termination outcome, not whether the root cause was
a leak, startup allocation, traffic spike, or node-wide exhaustion. An older
`last_state` record alone does not prove an ongoing failure; correlate time,
current readiness, and increasing restarts.

Use `get_pod_logs_and_events(pod_name, namespace, container, previous=True)`
for the previous instance. This tool also includes `pod_details`, so empty logs
or missing events do not prevent checking termination state. It preserves logs
and pod details if event retrieval fails and returns `events_error`. Select a
named init container when that is the failing component.

For a Deployment, inspect `get_deployment_details(deployment_name, namespace)`
to compare its template resource settings, replica health, and rollout
conditions with the actual pod. Pod ownership may point to a ReplicaSet;
identify the owning Deployment rather than assuming their names are identical.
Use `get_node_details(node_name)` to inspect `MemoryPressure`, capacity, and
allocatable resources. Those values are not live memory usage or historical
peaks. Historical metrics, memory profiles, namespace policies, and node kernel
OOM evidence still require other monitoring or operator evidence.

The tools are read-only: recommend remediation using retrieved guidance, but
do not claim to change memory settings, restart a workload, or fix a leak.
