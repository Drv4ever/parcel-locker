import json
import boto3
import time
import os

s3 = boto3.client('s3', region_name=os.environ.get('AWS_REGION', 'ap-south-1'))
BUCKET = os.environ.get('AUDIT_BUCKET', 'parcel-audit-logs-dhruv-652872010155')

def lambda_handler(event, context):
    print(f"Audit received event: {json.dumps(event)}")
    for record in event.get('Records', []):
        msg_str = record.get('Sns', {}).get('Message', '{}')
        msg = json.loads(msg_str)
        parcel_id = msg.get('parcelId', 'unknown')
        now_sec = int(time.time())
        key = f"audit/{parcel_id}-{now_sec}.json"
        
        audit_payload = {
            'event': 'parcel_logged',
            'timestamp': now_sec,
            'data': msg,
            'snsMessageId': record.get('Sns', {}).get('MessageId')
        }
        
        s3.put_object(
            Bucket=BUCKET,
            Key=key,
            Body=json.dumps(audit_payload, indent=2),
            ContentType='application/json'
        )
        print(f"Successfully stored immutable audit log in s3://{BUCKET}/{key}")

    return {'statusCode': 200}