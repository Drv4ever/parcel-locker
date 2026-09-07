import boto3
import time
import os

dynamodb = boto3.resource('dynamodb', region_name=os.environ.get('AWS_REGION', 'ap-south-1'))
table = dynamodb.Table('ParcelTable')

TEN_MIN = 10 * 60 * 1000
FORTY_EIGHT_HR = 48 * 60 * 60 * 1000
FIVE_DAYS = 5 * 24 * 60 * 60 * 1000

def lambda_handler(event, context):
    now = int(time.time() * 1000)
    print(f"Starting scheduled audit scan at epoch {now}")

    # Check pending_notification stuck > 10 min
    resp = table.query(
        IndexName='status-index',
        KeyConditionExpression=boto3.dynamodb.conditions.Key('status').eq('pending_notification')
    )
    for item in resp.get('Items', []):
        if now - item.get('createdAt', now) > TEN_MIN:
            print(f"Flagging {item['parcelId']} as FAILED_TO_NOTIFY")
            _update(item['parcelId'], 'FAILED_TO_NOTIFY', now)

    # Check notified stuck > 48 hr
    resp = table.query(
        IndexName='status-index',
        KeyConditionExpression=boto3.dynamodb.conditions.Key('status').eq('notified')
    )
    for item in resp.get('Items', []):
        if now - item.get('lastUpdatedAt', now) > FORTY_EIGHT_HR:
            print(f"Escalating {item['parcelId']} to reminder_sent")
            _update(item['parcelId'], 'reminder_sent', now)

    # Check reminder_sent stuck > 5 days
    resp = table.query(
        IndexName='status-index',
        KeyConditionExpression=boto3.dynamodb.conditions.Key('status').eq('reminder_sent')
    )
    for item in resp.get('Items', []):
        if now - item.get('lastUpdatedAt', now) > FIVE_DAYS:
            print(f"Escalating {item['parcelId']} to escalated")
            _update(item['parcelId'], 'escalated', now)

    print("Audit scan complete.")
    return {'statusCode': 200, 'message': 'Audit completed successfully'}

def _update(parcel_id, new_status, now):
    table.update_item(
        Key={'parcelId': parcel_id},
        UpdateExpression='SET #s = :s, lastUpdatedAt = :t',
        ExpressionAttributeNames={'#s': 'status'},
        ExpressionAttributeValues={':s': new_status, ':t': now}
    )