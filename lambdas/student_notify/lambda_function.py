import json
import boto3
import os

ses = boto3.client('ses', region_name=os.environ.get('AWS_REGION', 'ap-south-1'))
VERIFIED_SENDER = os.environ.get('VERIFIED_SENDER', '')

def lambda_handler(event, context):
    print(f"Student Notify received event: {json.dumps(event)}")
    for record in event.get('Records', []):
        msg_str = record.get('Sns', {}).get('Message', '{}')
        msg = json.loads(msg_str)
        student_id = msg.get('studentId', 'Unknown')
        parcel_id = msg.get('parcelId', 'Unknown')
        qr_code = msg.get('qrCode', 'N/A')
        courier = msg.get('courierName', 'Unknown')

        print(f"[NOTIFICATION SENT] Student: {student_id} | Parcel: {parcel_id} | Courier: {courier} | Code: {qr_code}")

        # If a verified sender is configured in SES, send an actual email
        if VERIFIED_SENDER:
            try:
                recipient = f"{student_id}@vitstudent.ac.in"
                ses.send_email(
                    Source=VERIFIED_SENDER,
                    Destination={'ToAddresses': [recipient]},
                    Message={
                        'Subject': {'Data': f'Parcel Arrived - {courier}'},
                        'Body': {'Text': {'Data': f"Hi {student_id},\n\nYour parcel from {courier} has arrived at the hostel locker.\nYour pickup verification code is: {qr_code}\n\nPlease present this code at the warden desk to collect your parcel."}}
                    }
                )
                print(f"Successfully sent SES email to {recipient}")
            except Exception as e:
                print(f"SES email delivery skipped/failed: {str(e)}")

    return {'statusCode': 200}