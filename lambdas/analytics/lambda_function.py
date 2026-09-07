import json
import boto3
import os

cloudwatch = boto3.client('cloudwatch', region_name=os.environ.get('AWS_REGION', 'ap-south-1'))

def lambda_handler(event, context):
    print(f"Analytics received event: {json.dumps(event)}")
    count = 0
    for record in event.get('Records', []):
        msg_str = record.get('Sns', {}).get('Message', '{}')
        msg = json.loads(msg_str)
        print(f"Recording metric for parcelId: {msg.get('parcelId')}")
        count += 1

    if count > 0:
        cloudwatch.put_metric_data(
            Namespace='ParcelSystem',
            MetricData=[{
                'MetricName': 'ParcelsLogged',
                'Value': count,
                'Unit': 'Count'
            }]
        )
        print(f"Successfully pushed metric ParcelsLogged={count} to CloudWatch")

    return {'statusCode': 200}