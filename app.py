import streamlit as st
import pandas as pd
import shotstack_sdk as shotstack
from shotstack_sdk.api import edit_api
from shotstack_sdk.model.template_render import TemplateRender
from shotstack_sdk.model.merge_field import MergeField
import requests
import os
import shutil
import time
import zipfile
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
from google.oauth2.credentials import Credentials
from googleapiclient.errors import HttpError
from oauth2client.client import flow_from_clientsecrets
from oauth2client.file import Storage
from oauth2client.tools import run_flow
import sys
import json
import boto3
from PIL import Image
from io import BytesIO
import matplotlib.pyplot as plt
import numpy as np
from fpdf import FPDF
import uuid
from helper import process_video
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
import os
import subprocess

st.set_page_config(
    page_title='VideoEditor',
    page_icon='📹'
) 
hide_streamlit_style = """ <style> #MainMenu {visibility: hidden;} footer {visibility: hidden;} </style> """ 
st.markdown(hide_streamlit_style, unsafe_allow_html=True)

def extract_id_from_url(url):
    match = re.search(r'(?<=folders/)[a-zA-Z0-9_-]+', url)
    if match:
        return match.group(0)
    match = re.search(r'(?<=spreadsheets/d/)[a-zA-Z0-9_-]+', url)
    if match:
        return match.group(0)
    return None

def reset_s3():
    # Set AWS details (replace with your own details)
    AWS_REGION_NAME = 'us-east-2'
    AWS_ACCESS_KEY = 'AKIARK3QQWNWXGIGOFOH'
    AWS_SECRET_KEY = 'ClAUaloRIp3ebj9atw07u/o3joULLY41ghDiDc2a'

    # Initialize the S3 client
    s3 = boto3.client('s3',
        region_name=AWS_REGION_NAME,
        aws_access_key_id=AWS_ACCESS_KEY,
        aws_secret_access_key=AWS_SECRET_KEY
    )

    # Delete objects within subdirectories in the bucket 'li-general-tasks'
    subdirs = ['input_videos/', 'output_videos/', 'images/']
    for subdir in subdirs:
        objects = s3.list_objects_v2(Bucket='li-general-tasks', Prefix=subdir)
        for obj in objects.get('Contents', []):
            if obj['Key'] != 'input_videos/outro.mp4':
                s3.delete_object(Bucket='li-general-tasks', Key=obj['Key'])
                
        # Add a placeholder object to represent the "directory"
        s3.put_object(Bucket='li-general-tasks', Key=subdir)

try:
    
    if 'begin_auth' not in st.session_state:
        reset_s3()
        st.session_state['creds'] = ""
        st.session_state['begin_auth'] = False
        st.session_state['final_auth'] = False

except:
    pass

# Title of the app
st.title("LI Video Editor")
st.caption("By Giacomo Pugliese")

with st.expander("Click to view full directions for this site"):
    st.subheader("Google Authentication")
    st.write("- Click 'Authenticate Google Account', and then on the generated link.")
    st.write("- Follow the steps of Google login until you get to the final page.")
    st.write("- Click on 'Finalize Authentication' to proceed to rest of website.")
    st.subheader("Video Intro Generator")
    st.write("- Enter the intended output Google drive folder link, as well as the program name of the students.")
    st.write("- If a solo intern video, upload a csv with columns PRECISELY titled 'name', 'school', 'location', and 'class'.")
    st.write("- If a group video, upload a csv with columns PRECISELY titled 'name1', 'name2', 'name3'.... (max 7 interns).")
    st.write("- Click 'Process Videos' to begin intro video renderings and view them in your destination Google drive folder.")
    st.subheader("Video Stitcher")
    st.write("- Enter the intended output Google drive folder link")
    st.write("- Upload a csv with columns PRECISELY titled 'name', 'intro', and 'main' (reffering to the intro and main video share links).")
    st.write("- Click 'Stitch Videos' to begin video stitching and view them in your destination Google drive folder.")
    st.subheader("Automatic Youtube Uploader")
    st.write("- Upload a csv with columns PRECISELY titled 'title' and 'video' (the video column should have a Google drive share link).")
    st.write("- Click 'Upload videos to youtube' and view them in your youtube channel.")

st.header("Google Authentication")

try:
    if st.button("Authenticate Google Account"):
        st.session_state['begin_auth'] = True
        # Request OAuth URL from the FastAPI backend
        response = requests.get(f"{'https://leadership-initiatives-0c372bea22f2.herokuapp.com'}/auth?user_id={'intros'}")
        if response.status_code == 200:
            # Get the authorization URL from the response
            auth_url = response.json().get('authorization_url')
            st.markdown(f"""
                <a href="{auth_url}" target="_blank" style="color: #8cdaf2;">
                    Click to continue to authentication page (before finalizing)


                </a>
                """, unsafe_allow_html=True)
            st.text("\n\n\n")
            # Redirect user to the OAuth URL
            # nav_to(auth_url)

    if st.session_state['begin_auth']:    
        if st.button("Finalize Google Authentication"):
            with st.spinner("Finalizing authentication..."):
                for i in range(6):
                    # Request token from the FastAPI backend
                    response = requests.get(f"{'https://leadership-initiatives-0c372bea22f2.herokuapp.com'}/token/{'intros'}")
                    if response.status_code == 200:
                        st.session_state['creds'] = response.json().get('creds')
                        print(st.session_state['creds'])
                        st.success("Google account successfully authenticated!")
                        st.session_state['final_auth'] = True
                        break
                    time.sleep(1)
            if not st.session_state['final_auth']:
                st.error('Experiencing network issues, please refresh page and try again.')
                st.session_state['begin_auth'] = False

except Exception as e:
    pass

st.header("Video Intro Generator")

col1, col2 = st.columns(2)

with col1:
    # Get the ID of the Google Drive folder to upload the videos to
    folder_id = st.text_input("URL of the Google Drive folder to upload the videos to:")

with col2:
    # Text input for the program name
    program = st.text_input("Enter the Program Name:")

# File upload widget
uploaded= st.file_uploader(label="Upload a CSV file", type=['csv'])

# Configure the Shotstack API
configuration = shotstack.Configuration(host = "https://api.shotstack.io/v1")
configuration.api_key['DeveloperKey'] = "ymfTz2fdKw58Oog3dxg5haeUtTOMDfXH4Qp9zlx2"

video_button = st.button("Process Videos")

if uploaded is not None and program and video_button and st.session_state['final_auth']:
    folder_id = extract_id_from_url(folder_id)
    # Load the CSV file into a dataframe
    dataframe = pd.read_csv(uploaded)

    # Create API client
    with shotstack.ApiClient(configuration) as api_client:
        api_instance = edit_api.EditApi(api_client)

        progress_report = st.empty()
        i = 1
        # Loop over the rows of the dataframe
        for _, row in dataframe.iterrows():

            # Create the merge fields for this row
            merge_fields = [
                MergeField(find="program_name", replace=program),
                MergeField(find="name", replace=row.get('name', row.get('name1', ''))),
                MergeField(find="school", replace=row.get('school', row.get('name2', ''))),
                MergeField(find="location", replace=row.get('location', row.get('name3', ''))),
                MergeField(find="class", replace='Class of ' + str(round(row['class'])) if 'class' in row else row.get('name4', '')),
                MergeField(find="name5", replace=row.get('name5', '')),
                MergeField(find="name6", replace=row.get('name6', '')),
                MergeField(find="name7", replace=row.get('name7', '')),
            ]

            # Create the template render object
            template = TemplateRender(
                id = "775d5f85-71f6-4e47-9e10-6c9eb0c0f477",
                merge = merge_fields
            )

            try:
                # Post the template render
                api_response = api_instance.post_template_render(template)

                # Display the message
                message = api_response['response']['message']
                id = api_response['response']['id']
                print(f"{message}")

                # Poll the API until the video is ready
                status = 'queued'
                while status != 'done':
                    time.sleep(1)
                    status_response = api_instance.get_render(id)
                    status = status_response.response.status
                    print(status)
                # Construct the video URL
                video_url = f"https://cdn.shotstack.io/au/v1/yn3e0zspth/{id}.mp4"

                print(video_url)

                name = row.get('name', row.get('name1', 'unnamed'))
                video_file = f"Videos/{name}.mp4"

                # Directly write the downloaded content to a file
                r = requests.get(video_url)
                with open(video_file, 'wb') as f:
                    f.write(r.content)

                # Append intro video to the beginning
                intro_video_path = "intro_li.mp4"
                main_video = mp.VideoFileClip(video_file)
                intro_video = mp.VideoFileClip(intro_video_path)

                print(f"Intro video duration: {intro_video.duration}, fps: {intro_video.fps}")
                print(f"Main video duration: {main_video.duration}, fps: {main_video.fps}")

                # Concatenate intro and the main video
                concatenated_video = mp.concatenate_videoclips([intro_video, main_video])

                # Load intro_audio.mp3 and set it as the audio of the final video
                audio_clip = mp.AudioFileClip("intro_audio.mp3")
                final_video = concatenated_video.set_audio(audio_clip)

                # Get main video file name and append 'intro_' to the beginning
                main_video_name, main_video_ext = os.path.splitext(os.path.basename(main_video.filename))
                new_video_name = f"{main_video_name}_intro{main_video_ext}"

                # Write the result to a file.
                final_video.write_videofile(new_video_name, codec='libx264')  

                # Google Drive service setup
                CLIENT_SECRET_FILE = 'credentials.json'
                API_NAME = 'drive'
                API_VERSION = 'v3'
                SCOPES = ['https://www.googleapis.com/auth/drive.readonly',
                            'https://www.googleapis.com/auth/youtube.upload']


                with open(CLIENT_SECRET_FILE, 'r') as f:
                    client_info = json.load(f)['web']

                creds_dict = st.session_state['creds']
                creds_dict['client_id'] = client_info['client_id']
                creds_dict['client_secret'] = client_info['client_secret']
                creds_dict['refresh_token'] = creds_dict.get('_refresh_token')

                # Create Credentials from creds_dict
                creds = Credentials.from_authorized_user_info(creds_dict)

                # Build the Google Drive service
                drive_service = build('drive', 'v3', credentials=creds)

                # Create a media file upload object
                media = MediaFileUpload(new_video_name, mimetype='video/mp4')

                # Create the file on Google Drive
                request = drive_service.files().create(
                    media_body=media,
                    body={
                        'name': new_video_name,
                        'parents': [folder_id]
                    }
                )

                time.sleep(2)
                # Execute the request
                file = request.execute()
                del media  # Explicitly delete the media object

                # Print the ID of the uploaded file
                print('File ID: %s' % file.get('id'))

                # Remove temporary file
                # os.remove(video_file)
                os.remove(new_video_name)
            except Exception as e:
                print(f"Unable to generate intro video for {video_file}: {e}")

            progress_report.text(f"Video progress: {i}/{len(dataframe)}")
            i+=1
    st.success("Videos successfully generated!")
    
# Streamlit UI
st.header("Video Stitcher")
stitch_folder = st.text_input("URL of the Google Drive folder to upload videos to:")

# File upload widget
stitch_uploaded = st.file_uploader(label="Upload a CSV file of videos", type=['csv'])

# Get user's local "Videos" directory
videos_directory = os.path.join(os.getcwd(), 'Videos')

stitch_button = st.button("Stitch Videos")

if stitch_button and st.session_state['final_auth'] and stitch_folder and stitch_uploaded is not None:
    stitch_folder = extract_id_from_url(stitch_folder)
    df = pd.read_csv(stitch_uploaded)

    # Assuming that 'CLIENT_SECRET_FILE', 'videos_directory', 'stitch_folder', and 'df' are defined elsewhere in your code

    CLIENT_SECRET_FILE = 'credentials.json'
    with open(CLIENT_SECRET_FILE, 'r') as f:
        client_info = json.load(f)['web']
    creds_dict = st.session_state['creds']
    creds_dict['client_id'] = client_info['client_id']
    creds_dict['client_secret'] = client_info['client_secret']
    creds_dict['refresh_token'] = creds_dict.get('_refresh_token')

    arguments = [(index, row, videos_directory, creds_dict, stitch_folder) for index, row in df.iterrows()]

    stitch_progress = st.empty()
    stitch_progress.text(f"Video Progress: 0/{len(df)}")

    i = 0

    with ProcessPoolExecutor(max_workers=15) as executor:
        futures = [executor.submit(process_video, arg) for arg in arguments]

        for future, arg in zip(as_completed(futures), arguments):
            try:
                result = future.result()
                i += 1
                stitch_progress.text(f"Video Progress: {i}/{len(arguments)}")
            except Exception as e:
                # Assuming the 'arg' is a tuple and the first element is the row number
                row_number = arg[0]
                print(f'Exception at row {row_number + 2}: {e}')

# Define the required scopes
SCOPES = ['https://www.googleapis.com/auth/drive.readonly',
          'https://www.googleapis.com/auth/youtube.upload']

def get_authenticated_service():
    flow = flow_from_clientsecrets('credentials.json',
        scope=SCOPES,
        message='Please configure OAuth 2.0')

    # Set the redirect_uri property of the flow object
    flow.redirect_uri = "https://photo-labeler-842ac8d73e7a.herokuapp.com/callback"

    storage = Storage("%s-oauth2.json" % sys.argv[0])
    credentials = storage.get()

    if credentials is None or credentials.invalid:
        credentials = run_flow(flow, storage)

    return build('youtube', 'v3', credentials=credentials)

def initialize_upload(youtube, video_file, title, description, category_id, tags):
    body = {
        'snippet': {
            'title': title,
            'description': description,
            'tags': tags,
            'categoryId': category_id
        },
        'status': {
            'privacyStatus': 'public'
        }
    }
    media = MediaFileUpload(video_file, mimetype='video/mp4', resumable=True)
    request = youtube.videos().insert(
        part=','.join(body.keys()),
        body=body,
        media_body=media
    )
    resumable_upload(request)

def resumable_upload(request):
    response = None
    while response is None:
        status, response = request.next_chunk()
        if response is not None:
            if 'id' in response:
                print(f'Video ID {response["id"]} was successfully uploaded.')
            else:
                print(f'The upload failed with an unexpected response: {response}')

def download_video_from_drive(url, output, creds_dict):
    creds = Credentials.from_authorized_user_info(creds_dict, SCOPES)
    drive_service = build('drive', 'v3', credentials=creds)
    file_id = url.split('/')[-2]
    video_request = drive_service.files().get_media(fileId=file_id)
    video_data_io = BytesIO()
    downloader = MediaIoBaseDownload(video_data_io, video_request)
    done = False
    while done is False:
        status, done = downloader.next_chunk()
    with open(output, 'wb') as f:
        f.write(video_data_io.getvalue())

st.header("Automatic Youtube Uploader")
video_uploads = st.file_uploader(label="Upload a CVS of videos", type=['csv'])
if st.button("Upload videos to youtube") and video_uploads:
    youtube = get_authenticated_service()
    df = pd.read_csv(video_uploads)
    CLIENT_SECRET_FILE = 'credentials.json'
    with open(CLIENT_SECRET_FILE, 'r') as f:
        client_info = json.load(f)['web']
    creds_dict = st.session_state['creds']
    creds_dict['client_id'] = client_info['client_id']
    creds_dict['client_secret'] = client_info['client_secret']
    creds_dict['refresh_token'] = creds_dict.get('_refresh_token')
    progress = st.empty()
    i = 0
    progress.text(f"Upload progress: {i}/{len(df)}")
    with st.spinner("Uploading videos..."):
        for index, row in df.iterrows():
            video_url = row['video']
            video_file = f"video_{index}.mp4"
            download_video_from_drive(video_url, video_file, creds_dict) 
            title = row['title']
            description = ""
            category_id = "22"
            tags = []
            try:
                initialize_upload(youtube, video_file, title, description, category_id, tags)
            except HttpError as e:
                st.write(f"Youtube API Rate limit exceeded.")
                break
            progress.text(f"Upload progress: {i}/{len(df)}")
            i+=1
            os.remove(video_file)

        