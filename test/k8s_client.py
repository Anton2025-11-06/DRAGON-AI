from kubernetes import client, config
from kubernetes.client import Configuration

# 加载kubeconfig
config.load_kube_config("./config")

# 关闭ssl验证
cfg = Configuration.get_default_copy()
cfg.verify_ssl = False
api_client = client.ApiClient(cfg)

v1 = client.CoreV1Api(api_client)
print("Listing pods with their IPs:")
ret = v1.list_pod_for_all_namespaces(watch=False)
for i in ret.items:
    print("%s\t%s\t%s" % (i.status.pod_ip, i.metadata.namespace, i.metadata.name))
