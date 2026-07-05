from src.mock_infra.aws_clients import AWSClientFactory


def test_s3_client_is_mocked_and_functional():
    with AWSClientFactory(use_mocks=True) as factory:
        s3 = factory.get_s3_client()
        s3.create_bucket(Bucket="test-bucket")
        buckets = s3.list_buckets()["Buckets"]
        assert any(b["Name"] == "test-bucket" for b in buckets)


def test_dynamodb_resource_is_mocked_and_functional():
    with AWSClientFactory(use_mocks=True) as factory:
        dynamodb = factory.get_dynamodb_resource()
        table = dynamodb.create_table(
            TableName="test-table",
            KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        table.wait_until_exists()

        table.put_item(Item={"id": "1", "value": "hello"})
        item = table.get_item(Key={"id": "1"})["Item"]
        assert item["value"] == "hello"


def test_sns_client_is_mocked_and_functional():
    with AWSClientFactory(use_mocks=True) as factory:
        sns = factory.get_sns_client()
        topic = sns.create_topic(Name="test-topic")
        topics = sns.list_topics()["Topics"]
        assert any(t["TopicArn"] == topic["TopicArn"] for t in topics)


def test_mocks_do_not_leak_between_factory_instances():
    with AWSClientFactory(use_mocks=True) as factory:
        s3 = factory.get_s3_client()
        s3.create_bucket(Bucket="leaky-bucket")

    with AWSClientFactory(use_mocks=True) as factory:
        s3 = factory.get_s3_client()
        buckets = s3.list_buckets()["Buckets"]
        assert not any(b["Name"] == "leaky-bucket" for b in buckets)
