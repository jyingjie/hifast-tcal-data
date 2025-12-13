
import os
import glob
import sys
import mimetypes

try:
    import boto3
    from botocore.exceptions import NoCredentialsError
except ImportError:
    print("Error: 'boto3' is required. Install it using: pip install boto3")
    sys.exit(1)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    print("Warning: python-dotenv not installed. Env vars must be set manually.")

# Configuration
# Users should set these environment variables or edit the script
R2_ENDPOINT_URL = os.environ.get("R2_ENDPOINT_URL", "https://<accountid>.r2.cloudflarestorage.com")
R2_ACCESS_KEY_ID = os.environ.get("R2_ACCESS_KEY_ID")
R2_SECRET_ACCESS_KEY = os.environ.get("R2_SECRET_ACCESS_KEY")
BUCKET_NAME = os.environ.get("R2_BUCKET_NAME", "hifast-tcal")
PREFIX = os.environ.get("R2_PREFIX", "") # e.g. "tcal-data"

# Directory containing the generated zips and manifest.json
# (Use the current directory where this script resides)
LOCAL_DATA_DIR = os.path.dirname(os.path.abspath(__file__))

def upload_file(s3_client, local_path, object_key):
    """Uploads a file to R2/S3 with proper public-read ACL if supported"""
    print(f"Uploading {local_path} -> s3://{BUCKET_NAME}/{object_key} ...")
    
    content_type, _ = mimetypes.guess_type(local_path)
    if content_type is None:
        content_type = 'application/octet-stream'
        
    try:
        # R2 doesn't always support ACLs depending on bucket config, 
        # but 'public-read' is common for public buckets. 
        # If your bucket is strictly private behind Cloudflare Worker/Public Access, remove ACL.
        extra_args = {'ContentType': content_type}
        
        # Uncomment if you need ACLs
        # extra_args['ACL'] = 'public-read' 

        s3_client.upload_file(
            local_path, 
            BUCKET_NAME, 
            object_key, 
            ExtraArgs=extra_args
        )
        print("  -> Success")
        return True
    except NoCredentialsError:
        print("  -> Error: Credentials not available")
        return False
    except Exception as e:
        print(f"  -> Error: {e}")
        return False

def main():
    if not R2_ACCESS_KEY_ID or not R2_SECRET_ACCESS_KEY:
        print("Please set R2_ACCESS_KEY_ID and R2_SECRET_ACCESS_KEY environment variables.")
        sys.exit(1)

    print(f"Connecting to R2 Endpoint: {R2_ENDPOINT_URL}")
    s3 = boto3.client(
        's3',
        endpoint_url=R2_ENDPOINT_URL,
        aws_access_key_id=R2_ACCESS_KEY_ID,
        aws_secret_access_key=R2_SECRET_ACCESS_KEY
    )

    if not os.path.exists(LOCAL_DATA_DIR):
        print(f"Error: Data directory {LOCAL_DATA_DIR} does not exist.")
        print("Run prepare_release.py first.")
        sys.exit(1)

    # 1. Upload manifest.json
    manifest_path = os.path.join(LOCAL_DATA_DIR, "manifest.json")
    if os.path.exists(manifest_path):
        key = f"{PREFIX}/manifest.json" if PREFIX else "manifest.json"
        # Remove leading slash if present to avoid double slashes issues in some clients
        if key.startswith("/"): key = key[1:]
        
        upload_file(s3, manifest_path, key)
    else:
        print(f"Warning: manifest.json not found in {LOCAL_DATA_DIR}")

    # 2. Upload Zip files
    # Structure: PREFIX/20190115/20190115.zip
    
    zip_files = sorted(glob.glob(os.path.join(LOCAL_DATA_DIR, "*.zip")))
    for zip_path in zip_files:
        filename = os.path.basename(zip_path) # e.g. 20190115.zip
        date_str = os.path.splitext(filename)[0] # e.g. 20190115
        
        # Object Key: [PREFIX/]folder/filename
        object_key = f"{date_str}/{filename}"
        if PREFIX:
             object_key = f"{PREFIX}/{object_key}"
             if object_key.startswith("/"): object_key = object_key[1:]
        
        upload_file(s3, zip_path, object_key)

    print("\nUpload complete.")
    prefix_path = f"/{PREFIX}" if PREFIX else ""
    print("Ensure your R2 bucket is configured for public access or has a Cloudflare Worker attached.")
    print(f"Example Public URL: https://<your-custom-domain>{prefix_path}/20190115/20190115.zip")

if __name__ == "__main__":
    main()
