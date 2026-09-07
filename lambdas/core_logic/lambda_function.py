import boto3
import uuid
import time
import json
import os

dynamodb = boto3.resource('dynamodb', region_name=os.environ.get('AWS_REGION', 'ap-south-1'))
table = dynamodb.Table('ParcelTable')
sns = boto3.client('sns', region_name=os.environ.get('AWS_REGION', 'ap-south-1'))
TOPIC_ARN = os.environ['TOPIC_ARN']

def lambda_handler(event, context):
    print(f"Received event: {json.dumps(event)}")
    action = event.get('action')  # "log_parcel" or "scan_pickup"
    now = int(time.time() * 1000)

    if action == 'log_parcel':
        parcel_id = str(uuid.uuid4())
        item = {
            'parcelId': parcel_id,
            'studentId': event['studentId'],
            'status': 'pending_notification',
            'courierName': event.get('courierName', 'Unknown'),
            'photoUrl': event.get('photoUrl', ''),
            'createdAt': now,
            'lastUpdatedAt': now,
            'qrCode': str(uuid.uuid4())[:8]
        }
        table.put_item(Item=item)
        print(f"Saved initial item to DynamoDB: {parcel_id}")

        # Publish to SNS fanout — Student Notify, Analytics, Audit all react independently
        sns_resp = sns.publish(
            TopicArn=TOPIC_ARN,
            Message=json.dumps(item),
            MessageAttributes={
                'eventType': {'DataType': 'String', 'StringValue': 'parcel_logged'}
            }
        )
        print(f"Published to SNS: {sns_resp.get('MessageId')}")

        # Update status to notified
        table.update_item(
            Key={'parcelId': parcel_id},
            UpdateExpression='SET #s = :s, lastUpdatedAt = :t',
            ExpressionAttributeNames={'#s': 'status'},
            ExpressionAttributeValues={':s': 'notified', ':t': now}
        )
        print(f"Updated status of {parcel_id} to notified")
        return {
            'statusCode': 200,
            'body': json.dumps({'parcelId': parcel_id, 'status': 'notified', 'qrCode': item['qrCode']})
        }

    elif action == 'scan_pickup':
        parcel_id = event['parcelId']
        table.update_item(
            Key={'parcelId': parcel_id},
            UpdateExpression='SET #s = :s, lastUpdatedAt = :t',
            ExpressionAttributeNames={'#s': 'status'},
            ExpressionAttributeValues={':s': 'picked_up', ':t': now}
        )
        print(f"Updated status of {parcel_id} to picked_up")
        return {
            'statusCode': 200,
            'body': json.dumps({'parcelId': parcel_id, 'status': 'picked_up'})
        }

    return {
        'statusCode': 400,
        'body': json.dumps({'error': f'Unknown action: {action}'})
    }