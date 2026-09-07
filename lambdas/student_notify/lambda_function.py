import json
import boto3
import os

ses = boto3.client('ses', region_name=os.environ.get('AWS_REGION', 'ap-south-1'))

# Set the VERIFIED_SENDER env var to a SES-verified email address to enable
# real delivery. In SES sandbox mode both sender AND recipient must be verified.
# Leave unset during local testing — the Lambda will log a dry-run notification.
VERIFIED_SENDER = os.environ.get('VERIFIED_SENDER', '')


def lambda_handler(event, context):
    print(f"Student Notify received {len(event.get('Records', []))} record(s)")

    for record in event.get('Records', []):
        msg_str = record.get('Sns', {}).get('Message', '{}')
        msg = json.loads(msg_str)

        student_id = msg.get('studentId', 'Unknown')
        parcel_id  = msg.get('parcelId',  'Unknown')
        qr_code    = msg.get('qrCode',    'N/A')
        courier    = msg.get('courierName', 'Unknown')
        recipient  = f"{student_id}@vitstudent.ac.in"

        # NOTE on status design: core_logic sets status → 'notified' optimistically
        # right after sns.publish() succeeds, before this Lambda runs. That means
        # 'notified' reflects "SNS accepted the message", not "student received email".
        # A true delivery confirmation would require SES delivery notifications via
        # SNS → another Lambda, which is out of scope for this demo.
        print(f"[NOTIFICATION] Student: {student_id} | Parcel: {parcel_id} | "
              f"Courier: {courier} | Code: {qr_code} | Recipient: {recipient}")

        if not VERIFIED_SENDER:
            # Dry-run: SES not configured, log only.
            print("[DRY-RUN] VERIFIED_SENDER env var not set — email skipped. "
                  "Set it to a SES-verified address to enable real delivery.")
            continue

        try:
            resp = ses.send_email(
                Source=VERIFIED_SENDER,
                Destination={'ToAddresses': [recipient]},
                Message={
                    'Subject': {'Data': f'Parcel Arrived - {courier}'},
                    'Body': {
                        'Text': {
                            'Data': (
                                f"Hi {student_id},\n\n"
                                f"Your parcel from {courier} has arrived at the hostel locker.\n"
                                f"Your pickup verification code is: {qr_code}\n\n"
                                f"Please present this code at the warden desk to collect your parcel.\n\n"
                                f"Parcel ID (for support): {parcel_id}"
                            )
                        }
                    },
                },
            )
            print(f"SES email sent to {recipient} | MessageId: {resp.get('MessageId')}")

        except ses.exceptions.MessageRejected as e:
            # Hard bounce or SES sandbox recipient not verified — log with full
            # context so the state_auditor can later flag this parcel.
            print(f"[SES ERROR] MessageRejected for {recipient}: {e}. "
                  f"parcelId={parcel_id} | Verify the recipient in SES sandbox.")
        except Exception as e:
            # Other errors (throttle, network) — log and continue; Lambda's
            # async retry policy and the DLQ will handle persistent failures.
            print(f"[SES ERROR] Unexpected error sending to {recipient}: {type(e).__name__}: {e}. "
                  f"parcelId={parcel_id}")

    return {'statusCode': 200}