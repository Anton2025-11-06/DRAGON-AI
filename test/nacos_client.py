import asyncio

from v2.nacos import ListServiceParam, ServiceList

from common.common_nacos.nacos_client import NacosClient


async def main():
    nacos_service = NacosClient(
        user_name="nacos",
        password="Inforeiot2025.",
        server_address="10.88.129.3:8848",
        service_name="service_user",
        ip="192.168.127.12",
        port=9001,
        namespace_id="bigdata-python-service-test"
    )

    await nacos_service.init()
    await nacos_service.register_service()

    print(nacos_service.service_client)

    rq = ListServiceParam(namespace_id="bigdata-python-service-test")
    instances = nacos_service.service_client.list_services(rq)
    print(instances)

if __name__ == '__main__':
    asyncio.run(main())