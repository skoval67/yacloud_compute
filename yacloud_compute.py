# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

DOCUMENTATION = '''

    name: yacloud_compute
    plugin_type: inventory
    short_description: Yandex.Cloud compute inventory source
    requirements:
        - yandexcloud
    extends_documentation_fragment:
        - inventory_cache
        - constructed
    description:
        - Get inventory hosts from Yandex Cloud
        - Uses a YAML configuration file
    options:
        plugin:
            description: Token that ensures this is a source file for the plugin.
            required: True
            choices: ['yacloud_compute']
        service_account_file:
            description: Service Account Key file for yacloud connection. If the file is not specified, the IAM token from the environment variable YC_TOKEN is checked.
            default: ""
        yacloud_clouds:
            description: Names of clouds to get hosts from
            type: list
            default: []
        yacloud_folders:
            description: Names of folders to get hosts from
            type: list
            default: []
        yacloud_group_label:
            description: VM's label used for group assignment
            type: string
            default: ""
        use_public_ip:
            description: Use of an external address, if the virtual machine has one.
            default: False
'''

EXAMPLES = '''
    plugin: yacloud_compute
    service_account_file: key.json
    yacloud_clouds:
    - cloud-2
    yacloud_folders:
    - default
    yacloud_group_label: group
    ansible_user: admin
    ansible_ssh_private_key_file: ~/.ssh/id_ed25519
    use_public_ip: true
'''

from ansible.errors import AnsibleError
from ansible.plugins.inventory import BaseInventoryPlugin, Constructable, Cacheable
from ansible.module_utils.common.text.converters import to_native
import os
import json

try:
    import yandexcloud
    from yandex.cloud.compute.v1.instance_service_pb2_grpc import InstanceServiceStub
    from yandex.cloud.compute.v1.instance_service_pb2 import ListInstancesRequest
    from google.protobuf.json_format import MessageToDict
    from yandex.cloud.resourcemanager.v1.cloud_service_pb2 import ListCloudsRequest
    from yandex.cloud.resourcemanager.v1.cloud_service_pb2_grpc import CloudServiceStub
    from yandex.cloud.resourcemanager.v1.folder_service_pb2 import ListFoldersRequest
    from yandex.cloud.resourcemanager.v1.folder_service_pb2_grpc import FolderServiceStub
except ImportError:
    raise AnsibleError('The yacloud dynamic inventory plugin requires yandexcloud')

class InventoryModule(BaseInventoryPlugin, Constructable, Cacheable):

    NAME = 'yacloud_compute'

    def _get_ip_for_instance(self, instance):
        for interface in instance.get("networkInterfaces", []):
            address = interface.get("primaryV4Address") or {}

            nat = address.get("oneToOneNat") or {}
            public_ip = nat.get("address")
            private_ip = address.get("address")

            if self.get_option("use_public_ip"):
                return public_ip or private_ip

            return private_ip

        return None

    def _get_clouds(self):
        all_clouds = MessageToDict(self.cloud_service.List(ListCloudsRequest()))["clouds"]
        if self.get_option('yacloud_clouds'):
            all_clouds[:] = [x for x in all_clouds if x["name"] in self.get_option('yacloud_clouds')]
        self.clouds = all_clouds

    def _get_folders(self):
        all_folders = []
        for cloud in self.clouds:
            all_folders += MessageToDict(self.folder_service.List(ListFoldersRequest(cloud_id=cloud["id"])))["folders"]

        if self.get_option('yacloud_folders'):
            all_folders[:] = [x for x in all_folders if x["name"] in self.get_option('yacloud_folders')]

        self.folders = all_folders

    def _get_all_hosts(self):
        self.hosts = []
        for folder in self.folders:
            hosts = self.instance_service.List(ListInstancesRequest(folder_id=folder["id"]))
            dict_ = MessageToDict(hosts)

            if dict_:
                self.hosts += dict_["instances"]

    def _init_client(self):
        file = self.get_option('service_account_file')
        try:
            if file is not None:
                with open(file, 'r', encoding='utf-8') as f:
                    sa_key = json.load(f)
                sdk = yandexcloud.SDK(service_account_key=sa_key)
            else:
                iam_token = os.getenv("YC_TOKEN")
                if not iam_token:
                    raise AnsibleError("YC_TOKEN environment variable is not set")
                sdk = yandexcloud.SDK(iam_token=iam_token)        # if not token:
        except Exception as e:
            raise AnsibleError(f"Failed to initialize Yandex Cloud SDK: {e}") from e

        self.instance_service = sdk.client(InstanceServiceStub)
        self.folder_service = sdk.client(FolderServiceStub)
        self.cloud_service = sdk.client(CloudServiceStub)

    def _process_hosts(self):
        group_label = self.get_option('yacloud_group_label')

        for instance in self.hosts:
            labels = instance.get("labels") or {}

            if group_label and group_label in labels:
                group = labels[group_label]
            else:
                group = "yacloud"

            self.inventory.add_group(group=group)

            if instance.get("status") != "RUNNING":
                continue

            ip = self._get_ip_for_instance(instance)
            if not ip:
                continue

            hostname = instance["name"]
            self.inventory.add_host(hostname, group=group)
            self.inventory.set_variable(hostname, "ansible_host", to_native(ip))

    def parse(self, inventory, loader, path, cache=True):
        super(InventoryModule, self).parse(inventory, loader, path)

        self._read_config_data(path)
        self._init_client()

        self._get_clouds()
        self._get_folders()

        self._get_all_hosts()
        self._process_hosts()
