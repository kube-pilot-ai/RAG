---
title: "Troubleshooting Kubernetes pods: ImagePullBackOff"
document_id: pod-imagepullbackoff
resource: Pod
symptoms: [ImagePullBackOff, ErrImagePull, ImagePullOff]
reviewed_on: "2026-11-06"
---

# Troubleshooting Kubernetes pods: ImagePullBackOff

## Meaning and symptoms

`ImagePullBackOff` means Kubernetes cannot download a container image and is
waiting before another attempt. `ErrImagePull` may appear first. `ImagePullOff`
is a common informal spelling; the Kubernetes reason is `ImagePullBackOff`.
The failure affects an individual container, including an init container, and
does not necessarily describe every container in the pod. Retries back off up
to five minutes. See [Kubernetes image behavior](https://kubernetes.io/docs/concepts/containers/images/).

## Collect evidence

Identify the namespace, pod, affected container, configured image, and exact
pull error. Replace the example names below. Use `default` when no namespace
was specified.

```powershell
kubectl get pods -n default
kubectl describe pod example-pod -n default
kubectl get pod example-pod -n default -o yaml
kubectl get events -n default --field-selector involvedObject.kind=Pod,involvedObject.name=example-pod --sort-by=.metadata.creationTimestamp
```

Inspect both `status.containerStatuses` and `status.initContainerStatuses`,
especially `state.waiting.reason` and `state.waiting.message`. Check the image
reference in `spec.containers` or `spec.initContainers`. Events can expire;
capture the failure message promptly. An image that never started has no
application logs for that attempt.

## Diagnose by error message

| Evidence in pull error | Likely cause | Next action |
| --- | --- | --- |
| `manifest unknown`, `not found` | Wrong repository, missing tag, or unavailable digest | Confirm the exact reference exists in the intended registry |
| `unauthorized`, `denied` | Missing credentials or insufficient repository access | Check registry permissions and the credentials used by this pod |
| `FailedToRetrieveImagePullSecret` | Referenced Secret missing or inaccessible | Check Secret name and namespace |
| `no such host` | Node-side DNS failure | Check resolution of the registry hostname from the affected node |
| `i/o timeout`, connection refused | Registry outage or blocked network route | Check node egress, proxy settings, firewall, and registry availability |
| `x509` certificate error | Registry certificate or node trust problem | Correct certificates or the node's trusted CA configuration |
| `429`, `too many requests` | Registry throttling | Check registry quotas and authenticated pull configuration |
| `no matching manifest` | Image unavailable for the node's OS/architecture | Publish or select a compatible image |

These are diagnostic leads, not proof. Registry messages may conceal whether
a private repository exists. Use the full event message and registry-side
evidence. See [image pull troubleshooting](https://docs.cloud.google.com/kubernetes-engine/docs/troubleshooting/image-pulls).

## Resolve image and authentication problems

For a bad reference, correct the owning workload's image in its source manifest
and deploy the intended tag or immutable digest. For a Deployment, modify its
pod template rather than editing a controller-managed pod. Kubernetes retries
pulls; deleting a pod alone does not fix the reference.

For a private registry, inspect the pod's `imagePullSecrets` and ServiceAccount.
The referenced Secret must exist in the pod's namespace and use an appropriate
registry credential type. Check metadata without printing credential data:

```powershell
kubectl get secret registry-credentials -n default -o jsonpath='{.type}'
kubectl get serviceaccount default -n default -o yaml
```

Example Deployment fragment referencing an existing Secret:

```yaml
spec:
  template:
    spec:
      imagePullSecrets:
        - name: registry-credentials
```

Create or rotate credentials through your approved secret-management process.
Do not place passwords in these documents. Some registries use node identities
or credential providers instead of image pull Secrets; verify the mechanism
actually configured. See [private registry credentials](https://kubernetes.io/docs/tasks/configure-pod-container/pull-image-private-registry/).

## Verify recovery

After deploying the correction, inspect the replacement pod and the affected
container. Confirm it starts, becomes ready, and no new pull failures occur.
For a Deployment, also check:

```powershell
kubectl rollout status deployment/example-deployment -n default --timeout=120s
```

If the image pulls but the container then crashes, investigate the new failure
separately; successful image download does not establish application health.

## Using the current assistant

Use `list_pods(namespace)` or `get_pod_details(pod_name, namespace)` to inspect
regular and init containers. Check each container's `image`, `image_pull_policy`,
`state.waiting.reason`, and `state.waiting.message` for the exact failing image
and pull error. The pod details include its node, owners, service account,
conditions, and referenced `image_pull_secrets` names, without Secret values.
Missing container status means the runtime has not reported it yet.

Use `get_pod_logs_and_events(pod_name, namespace)` for UID-filtered events.
This also returns `pod_details` when logs cannot be read because the image
never started. An `events_error` indicates event retrieval failed, not that no
events occurred. Event absence alone does not rule out a pull failure.

Use `get_deployment_details(deployment_name, namespace)` to compare the desired
image and rollout health with the affected pod. `get_node_details(node_name)`
provides node conditions and OS/architecture information for compatibility
checks. Current tools do not inspect registry credential contents, registry
permissions, DNS/egress connectivity, or node certificate trust. Ask for
operator evidence for those checks. Never infer a Secret is valid just because
its name appears in the pod. Do not claim to repair an image or workload: the
MCP tools only read cluster information.
