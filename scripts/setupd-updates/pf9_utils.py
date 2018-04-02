# Copyright (c) Platform9 systems. All rights reserved

import json
import logging
import requests, os
from qbert import Qbert
from retry import retry

DEFAULT_INVALID_PRIMARY_IP = '0.0.0.0'
DEFAULT_POOL = 'defaultPool'
CLUSTER_FLANNEL_IFACE_LABEL = ''
CLUSTER_FLANNEL_PUBLIC_IFACE_LABEL = ''
PF9_SVC_NAME = 'pf9-kube'
CONTAINERS_CIDR = '172.31.0.0/20'
SERVICES_CIDR = '172.31.16.0/20'

DEFAULT_NAMESPACE = 'default'
SYSTEM_NAMESPACE = 'kube-system'

DEFAULT_RUNTIME_CONFIG = ''
ALL_APIS_RUNTIME_CONFIG='api/all=true'


LOG = logging.getLogger(__name__)
log = LOG

@retry(max_wait=600, interval=15, log=LOG)
def login(du_host, username, password, project_name):
    url = "https://%s/keystone/v3/auth/tokens?nocatalog" % du_host

    body = {
        "auth": {
            "identity": {
                "methods": ["password"],
                "password": {
                    "user": {
                        "name": username,
                        "domain": {"id": "default"},
                        "password": password
                    }
                }
            },
            "scope": {
                "project": {
                    "name": project_name,
                    "domain": {"id": "default"}
                }
            }
        }
    }
    LOG.info('Authenticating with keystone @ %s, user: %s, proj %s' % (du_host, username, project_name))
    resp = requests.post(url,
                         data=json.dumps(body),
                         headers={'content-type': 'application/json'},
                         verify=False)
    resp.raise_for_status()
    return resp.headers['X-Subject-Token']

@retry(max_wait=600, interval=15, log=LOG)
def create_cluster(du_host, master_ip, token, cluster_name):

    qbert_api_url = 'https://{0}/qbert/v1'.format(du_host)
    qbert = Qbert(token, qbert_api_url)
    clusters = qbert.list_clusters()
    #make cluster create idempotent
    if cluster_name not in clusters:
        nodepool_uuid = qbert.list_nodepools()[DEFAULT_POOL]['uuid']
        qbert.create_cluster({
            'name': cluster_name,
            'nodePoolUuid': nodepool_uuid,
            'containersCidr': CONTAINERS_CIDR,
            'servicesCidr': SERVICES_CIDR,
            'keystoneEnabled': True,
            'appCatalogEnabled': os.getenv('USE_APP_CATALOG') == 'true',
            'debug': 'true',
            'flannelIfaceLabel': CLUSTER_FLANNEL_IFACE_LABEL,
            'flannelPublicIfaceLabel': CLUSTER_FLANNEL_PUBLIC_IFACE_LABEL,
            'externalDnsName': master_ip
        })
        LOG.info('Creating cluster by name %s'%cluster_name)
        _wait_for_cluster_to_be_created(qbert, cluster_name)
    return cluster_name

def upgrade_cluster(token, du_host, cluster_name='defaultCluster'):
    qbert_api_url = 'https://{0}/qbert/v1'.format(du_host)
    qbert = Qbert(token, qbert_api_url)
    clusters = qbert.list_clusters()
    clusteruuid = clusters[cluster_name]['uuid']
    return qbert.upgrade_cluster(clusteruuid)

def attach_master_node(token, du_host, host_id, cluster_name='defaultCluster'):
    qbert_api_url = 'https://{0}/qbert/v1'.format(du_host)
    qbert = Qbert(token, qbert_api_url)

    return qbert.attach_nodes([host_id], cluster_name)


@retry(log=log, max_wait=30, interval=5)
def _wait_for_cluster_to_be_created(qbert, cluster_name):
    log.info('Waiting for cluster to be created')
    return cluster_name in qbert.list_clusters()


@retry(log=log, max_wait=240, interval=20)
def _wait_for_nodes_to_appear_in_qbert(qbert, node_names):
    log.info('Waiting for nodes to appear in qbert')
    return set(node_names) <= set(qbert.list_nodes())


@retry(log=log, max_wait=1200, interval=30, tolerate_exceptions=True)
def _wait_for_successful_attach(qbert, node_names, cluster_name):
    """Retry is needed so since attach_nodes() can fail if there's an
    existing master, and it is not in 'ok' state"""
    qbert.attach_nodes(node_names, cluster_name)
    return True


@retry(log=log, max_wait=120, interval=5, tolerate_exceptions=False)
def _wait_for_nodes_to_be_attached(qbert, cluster_name, node_names):
    log.info('Waiting for nodes to be attached')
    nodes = qbert.list_nodes()
    for node_name in node_names:
        if node_name not in nodes:
            log.info('Node %s not yet authorized.', node_name)
            return False
        if nodes[node_name]['clusterName'] != cluster_name:
            log.info('Node %s not yet attached to cluster %s',
                     node_name, cluster_name)
            return False
    return True

