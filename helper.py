import os
import subprocess
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload, MediaInMemoryUpload
from google.oauth2.credentials import Credentials
import os
import boto3
from botocore.exceptions import NoCredentialsError
from retry import retry
import time
from googleapiclient.errors import ResumableUploadError
import ssl
import re

def wait_for_s3_object(s3, bucket, key, local_filepath):
    """
    Wait for an S3 object to exist and have the same size as the local file.
    """
    local_filesize = os.path.getsize(local_filepath)
    while True:
        try:
            response = s3.head_object(Bucket=bucket, Key=key)
            if 'ContentLength' in response and response['ContentLength'] == local_filesize:
                return True
        except Exception as e:
            pass
        ("waited")
        time.sleep(1)
        
def concatenate_videos_aws(intro_resized_filename, main_filename, outro_resized_filename, output_filename, service, stitch_folder):
    # Set AWS details (replace with your own details)
    AWS_REGION_NAME = 'us-east-2'
    AWS_ACCESS_KEY = 'AKIARK3QQWNWXGIGOFOH'
    AWS_SECRET_KEY = 'ClAUaloRIp3ebj9atw07u/o3joULLY41ghDiDc2a'
    BUCKET_NAME = 'li-general-tasks'
    S3_INPUT_PREFIX = 'input_videos/'
    S3_OUTPUT_PREFIX = 'output_videos/'

    # Initialize the S3 client
    s3 = boto3.client('s3',
        region_name=AWS_REGION_NAME,
        aws_access_key_id=AWS_ACCESS_KEY,
        aws_secret_access_key=AWS_SECRET_KEY
    )

    # Create a unique identifier based on the main file name
    unique_id = main_filename.rsplit('_', 1)[0]


    # Initialize boto3 client for AWS MediaConvert
    client = boto3.client('mediaconvert',
        region_name=AWS_REGION_NAME,
        aws_access_key_id=AWS_ACCESS_KEY,
        aws_secret_access_key=AWS_SECRET_KEY,
        endpoint_url='https://fkuulejsc.mediaconvert.us-east-2.amazonaws.com'
    )

    def wait_for_job_completion(client, job_id):
        while True:
            response = client.get_job(Id=job_id)
            status = response['Job']['Status']
            if status in ['COMPLETE', 'ERROR', 'CANCELED']:
                return status
            time.sleep(5)

    # Define the input files and their roles
    inputs = [
        {
            'FileInput': f's3://{BUCKET_NAME}/{S3_INPUT_PREFIX}{intro_resized_filename}',
            'AudioSelectors': {
                'Audio Selector 1': {
                    'DefaultSelection': 'DEFAULT',
                    'SelectorType': 'TRACK',
                    'Offset': 0,
                    'ProgramSelection': 1,
                }
            }
        },
        {
            'FileInput': f's3://{BUCKET_NAME}/{S3_INPUT_PREFIX}{main_filename}',
            'AudioSelectors': {
                'Audio Selector 2': {
                    'DefaultSelection': 'DEFAULT',
                    'SelectorType': 'TRACK',
                    'Offset': 0,
                    'ProgramSelection': 1,
                }
            }
        },
        {
            'FileInput': f's3://{BUCKET_NAME}/{S3_INPUT_PREFIX}{outro_resized_filename}',
            'AudioSelectors': {
                'Audio Selector 3': {
                    'DefaultSelection': 'DEFAULT',
                    'SelectorType': 'TRACK',
                    'Offset': 0,
                    'ProgramSelection': 1,
                }
            }
        }
    ]

    output = {
        'Extension': 'mp4',
        'ContainerSettings': {
            'Container': 'MP4',
            'Mp4Settings': {
                'CslgAtom': 'INCLUDE',
                'FreeSpaceBox': 'EXCLUDE',
                'MoovPlacement': 'PROGRESSIVE_DOWNLOAD'
            }
        },
        'VideoDescription': {
            'CodecSettings': {
                'Codec': 'H_264',
                'H264Settings': {
                    'CodecProfile': 'MAIN',
                    'CodecLevel': 'AUTO',
                    'RateControlMode': 'QVBR',
                    'MaxBitrate': 5000000  # Example value, adjust as needed
                }
            }
        },
        'AudioDescriptions': [{
            'AudioSourceName': 'Audio Selector 1', # Specify the name of the audio selector
            'CodecSettings': {
                'Codec': 'AAC',
                'AacSettings': {
                    'AudioDescriptionBroadcasterMix': 'NORMAL',
                    'RateControlMode': 'CBR',
                    'CodecProfile': 'LC',
                    'CodingMode': 'CODING_MODE_2_0',
                    'SampleRate': 48000,
                    'Bitrate': 96000
                }
            }
        }]
    }

    # Create the job settings
    job_settings = {
        'Inputs': inputs,
        'OutputGroups': [{
            'Name': 'File Group',
            'Outputs': [output],
            'OutputGroupSettings': {
                'Type': 'FILE_GROUP_SETTINGS',
                'FileGroupSettings': {
                    'Destination': f's3://{BUCKET_NAME}/{S3_OUTPUT_PREFIX}{output_filename.rsplit(".", 1)[0]}'
                }
            }
        }]
    }


    try:
        max_retries = 4
        retry_count = 0

        while retry_count < max_retries:
            try:
                # Submit the job
                response = client.create_job(Role='arn:aws:iam::092040901485:role/media_convert', Settings=job_settings)
                job_id = response['Job']['Id']
                print('Job created:', job_id)

                # Wait for the job to finish
                job_status = wait_for_job_completion(client, job_id)
                if job_status == 'COMPLETE':
                    print('Job completed successfully.')
                    break
                elif job_status == 'ERROR':
                    response = client.get_job(Id=job_id)
                    error_message = response['Job'].get('ErrorMessage', 'No error message provided.')
                    print('Job failed with error:', error_message)
                    retry_count += 1
                    print(f"Retrying job submission... (Attempt {retry_count}/{max_retries})")
                else:
                    print(f'Job failed with status: {job_status}')
                    return
            except Exception as e:
                print('Error:', e)
                retry_count += 1
                print(f"Retrying job submission... (Attempt {retry_count}/{max_retries})")

        if retry_count == max_retries:
            print('Maximum number of retries reached. Job submission failed.')
            return
    
        # Use Google Drive API to upload the video from S3 to Google Drive
        s3_url = f'https://{BUCKET_NAME}.s3.amazonaws.com/{S3_OUTPUT_PREFIX}{output_filename.rsplit(".", 1)[0]}.mp4'
        response = requests.get(s3_url, stream=True)
        upload_video(response.raw, stitch_folder, service, output_filename)

        # Clean up the S3 bucket by deleting the files
        s3.delete_object(Bucket=BUCKET_NAME, Key=S3_INPUT_PREFIX + f'{unique_id}_intro.mp4')
        s3.delete_object(Bucket=BUCKET_NAME, Key=S3_INPUT_PREFIX + f'{unique_id}_main.mp4')
        s3.delete_object(Bucket=BUCKET_NAME, Key=S3_INPUT_PREFIX + f'{unique_id}_outro.mp4')
        s3.delete_object(Bucket=BUCKET_NAME, Key=S3_OUTPUT_PREFIX + output_filename.rsplit(".", 1)[0] + '.mp4')
        return


    except Exception as e:
        print('Error:', e)

    return

@retry((ssl.SSLEOFError, ResumableUploadError), tries=5, delay=2, backoff=2)
def download_video(file_id, filename, service):
    try:
        request = service.files().get_media(fileId=file_id)
        with open(filename, 'wb') as f:
            downloader = MediaIoBaseDownload(f, request)
            done = False
            while not done:
                status, done = downloader.next_chunk()
    except:
        print("retried!")
    
def upload_video(stream, folder_id, service, output_filename):
    file_metadata = {'name': output_filename, 'parents': [folder_id]}
    media = MediaInMemoryUpload(stream.read(), mimetype='video/mp4', resumable=True)
    try:
        return service.files().create(body=file_metadata, media_body=media, fields='id').execute()
    except (ssl.SSLEOFError, ResumableUploadError) as e:
        print(f"{e.__class__.__name__} encountered, retrying...")
        raise

import requests
from io import BytesIO

def process_video(data):
    row_number, row, videos_directory, creds_dict, stitch_folder = data
    creds = Credentials.from_authorized_user_info(creds_dict)
    service = build('drive', 'v3', credentials=creds)

    # Create a unique identifier based on the row name
    unique_id = row['name']

    # Set AWS details (replace with your own details)
    AWS_REGION_NAME = 'us-east-2'
    AWS_ACCESS_KEY = 'AKIARK3QQWNWXGIGOFOH'
    AWS_SECRET_KEY = 'ClAUaloRIp3ebj9atw07u/o3joULLY41ghDiDc2a'
    BUCKET_NAME = 'li-general-tasks'
    S3_INPUT_PREFIX = 'input_videos/'
    S3_OUTPUT_PREFIX = 'output_videos/'

    # Initialize the S3 client
    s3 = boto3.client('s3',
        region_name=AWS_REGION_NAME,
        aws_access_key_id=AWS_ACCESS_KEY,
        aws_secret_access_key=AWS_SECRET_KEY
    )

    # Stream the main video directly from Google Drive to S3
    main_file_id = row['main'].split("/file/d/")[1].split("/view")[0]
    stream_video_to_s3(service, main_file_id, f'{unique_id}_main.mp4', s3, BUCKET_NAME, S3_INPUT_PREFIX)

    # Stream the intro video directly from Google Drive to S3
    intro_file_id = row['intro'].split("/file/d/")[1].split("/view")[0]
    stream_video_to_s3(service, intro_file_id, f'{unique_id}_intro.mp4', s3, BUCKET_NAME, S3_INPUT_PREFIX)

    # Concatenate video clips
    output_filename = f"{row['name']}_final.mp4"
    concatenate_videos_aws(f'{unique_id}_intro.mp4', f'{unique_id}_main.mp4', "outro.mp4", output_filename, service, stitch_folder)

    return row['name']

def stream_video_to_s3(service, file_id, s3_filename, s3_client, bucket_name, s3_prefix):
    request = service.files().get_media(fileId=file_id)
    response = request.execute()
    stream = BytesIO(response)
    s3_client.upload_fileobj(stream, bucket_name, s3_prefix + s3_filename)

