import os
import sys

import pika


connection = pika.BlockingConnection(pika.ConnectionParameters(
    host=os.environ['RABBIT_HOST'], virtual_host="/oficina",
    credentials=pika.PlainCredentials("local_app", "local-rabbit-only"),
    connection_attempts=30, retry_delay=2,
))
channel = connection.channel()
channel.exchange_declare(exchange="oficina.fixture", exchange_type="direct", durable=True)
channel.queue_declare(queue="oficina.fixture", durable=True)
channel.queue_bind(queue="oficina.fixture", exchange="oficina.fixture", routing_key="fixture")
if sys.argv[1] == "publish":
    channel.confirm_delivery()
    channel.basic_publish(exchange="oficina.fixture", routing_key="fixture", body=b"persistent-fixture",
                          properties=pika.BasicProperties(delivery_mode=2), mandatory=True)
else:
    method, properties, body = channel.basic_get(queue="oficina.fixture", auto_ack=False)
    if method is None or body != b"persistent-fixture" or properties.delivery_mode != 2:
        raise RuntimeError("Durable AMQP message missing after restart")
    channel.basic_ack(method.delivery_tag)
connection.close()
print("PASS AMQP " + sys.argv[1])
