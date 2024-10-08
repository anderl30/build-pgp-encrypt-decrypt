# Python script to PGP decrypt file

# Import required packages
import json
import boto3
import gnupg
import botocore
import os
import pathlib
import gzip

from botocore.exceptions import ClientError
import logging


# Declare global clients
s3_client = boto3.client('s3')
secretsmanager_client = boto3.client('secretsmanager')

# Function to retrieve specified secret_value from secrets manager
def get_secret_details(secretArn, pgpKeyType):
    try:
        response = secretsmanager_client.get_secret_value(
           SecretId=secretArn
           )
        # Create dictionary
        secret = response['SecretString']
        if secret != None:
            secret_dict = json.loads(secret)
        else:
            print("Secrets Manager exception thrown")
            statusCode = 500
            body = {
                    "errorMessage": "Secrets Manager exception thrown"
                }
        if pgpKeyType in secret_dict:
            print("Private Key / Passphrase found!")
            PGPKey = secret_dict[pgpKeyType]
            PGPPassphrase = secret_dict['PGPPassphrase']
            statusCode = 200
        else:
            print(f"{pgpKeyType} not found in secret")
            statusCode = 500
            body = {
                "errorMessage": f"{pgpKeyType} not found in secret"
            }
        return {
            "PGPKey": PGPKey,
            "PGPPassphrase": PGPPassphrase
            }
    except ClientError as e:
        print(json.dumps(e.response))
        statusCode = e.response['ResponseMetadata']['HTTPStatusCode']
        errorCode = e.response['Error']['Code']
        errorMessage = e.response['Error']['Message']
        body = {
             "errorCode": errorCode,
             "errorMessage": errorMessage
        }
        return {
            'statusCode': statusCode,
            'body': body
        }

# Function that downloads file from S3 specified S3 bucket, returns a boolean indicating if file download was a success/failure
def downloadfile(bucketname, key, filename):
    try:
        newfilename = '/tmp/' + filename
        print(f"Trying to download key {key} from bucket {bucketname} to {newfilename}")
        # Download file from S3 to /tmp directory in lambda
        s3_client.download_file(bucketname, key, newfilename)
        # If download is successful, function returns true
        return os.path.exists(newfilename)

    except botocore.exceptions.ClientError as error:
        # Summary of what went wrong
        print(error.response['Error']['Code'])
        # Explanation of what went wrong
        print(error.response['Error']['Message'])
        # If download fails, function returns false
        return False

# Function that creates a temporary file within the /tmp directory.
def createtempfile():
    with open('/tmp/tempfile', 'w') as fp:
        pass


# Function that checks if the file is encrypted or not and returns corresponding boolean.
def checkEncryptionStatus(filename):
    file_extension = pathlib.Path(filename).suffix
    if (file_extension == '.asc' or file_extension == '.gpg' or file_extension == '.pgp'):
        print("This file is encrypted, performing decryption now.")
        return True
    else:
        print("This file is not GPG encrypted, no need to perform decryption.")
        return False

# Function that removes the .gpg or .asc file extension from encrypted files.
def remove_file_extension(filename):
    #if filename.lower().endswith('.gpg'):
    if checkEncryptionStatus(filename):
        decrypted_file_name = filename[:-4]  # Remove the last 4 characters (.gpg)
    else:
        decrypted_file_name = filename
    return decrypted_file_name


# Function that Checks if gzip first is required.
def checkUnzipFileStatus(filename):
    file_extension = pathlib.Path(filename).suffix
    if (file_extension == '.gz'):
        print("This file is compressed, need to unzip first.")
        return True
    else:
        print("This file is not compressed, no need to unzip file")
        return False

# Lambda handler
def handler(event, context):

    # Decryption requires PGP private key and passphrase
    pgpKeyType = 'PGPPrivateKey'

    # Get variables from event
    bucket = event['Records'][0]['s3']['bucket']['name']
    key = event['Records'][0]['s3']['object']['key']
    head_tail = os.path.split(key)
    final_path = head_tail[0].replace("/encrypt","")
    pgpSecret = '/kea/${Env}/s3decrypt/' + bucket

  
    # Set required file names
    file = key.split('/')[-1]
    decrypted_file_name = '/tmp/' + remove_file_extension(file)
    decrypted_file_without_tmp = remove_file_extension(file)
    decrypted_key = final_path

    # Download encrypted file from S3
    try:
        downloadStatus = downloadfile(bucket, key, file)

        # Store local file name for decryption
        local_file_name = '/tmp/' + file
        
        # If file downloads successfully, continue with process
        if downloadStatus:
            print("Download successful")

            # Check if file needs to be Unzipped first
            unzipFileStatus = checkUnzipFileStatus(local_file_name)

            if unzipFileStatus:
                # Gunzip the file
                unzipped_local_file_name = local_file_name[:-3]  # Remove .gz extension
                with gzip.open(local_file_name, 'rb') as f_in:
                    with open(unzipped_local_file_name, 'wb') as f_out:
                        f_out.write(f_in.read())
                print(f"File unzipped successfully: {unzipped_local_file_name}")
                local_file_name = unzipped_local_file_name          
            
            # Check file extension to see if file is encrypted
            encryptedStatus = checkEncryptionStatus(local_file_name)

            if encryptedStatus:

                # Create temp file
                createtempfile()

                # Remove .gpg file extension from file name
                updatedfilename = remove_file_extension(local_file_name)

                # Get PGP private key and passphrase from Secrets Manager
                pgpDetails = get_secret_details(pgpSecret, pgpKeyType)
                PGPPrivateKey = pgpDetails['PGPKey']
                PGPPassphrase = pgpDetails['PGPPassphrase']

                # Set GNUPG home directory and point to where the binary is stored.
                gpg = gnupg.GPG(gnupghome='/tmp', gpgbinary='/bin/gpg')
                print("GPG binary initialized successfully")

                # Import PGP private key into keyring
                print('Trying importing PGP private key')
                import_result = gpg.import_keys(PGPPrivateKey)
                print("PGP private Key imported successfully")

                with open(local_file_name, 'rb') as f:
                    status = gpg.decrypt_file(f, passphrase = PGPPassphrase, output = decrypted_file_name)

                # Print decryption status information to logs
                print("ok: ", status.ok)
                print("status: ", status.status)
                print("stderr: ", status.stderr)

                if (status.ok == True):
                    print("Status: OK")

                    # Upload decrypted file to S3
                    try:
                        print(f"Uploading file: {updatedfilename}, back to bucket: {bucket}, as key: {decrypted_key}")
                        s3response = s3_client.upload_file(decrypted_file_name, bucket, decrypted_key)
                        print("File uploaded successfully")
                    except ClientError as error:
                        # Summary of what went wrong
                        print(error.response['Error']['Code'])
                        # Explanation of what went wrong
                        print(error.response['Error']['Message'])
                        return False


            # Create JSON body response containing decrypted file S3 path for reference
            statusCode = 200
            response = {
                'statusCode': statusCode
            }

            # Return decrypted file name / S3 path to be passed to next step in step function
            return response

    # If file download from S3 is not successful, return error message
    except Exception as e:
        print(e)
        print('Error getting object {} from bucket {}. Make sure they exist and your bucket is in the same region as this function.'.format(key, bucket))
        raise
