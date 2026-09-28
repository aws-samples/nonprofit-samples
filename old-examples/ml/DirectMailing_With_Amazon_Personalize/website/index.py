from zipfile import ZipFile
import os
import boto3
import urllib3
import cfnresponse

http = urllib3.PoolManager()
ZIP_FILE_NAME = '/tmp/site.zip'

def lambda_handler(event, context):
    
    bucket = event["ResourceProperties"]["Bucket"]
    region = event["ResourceProperties"]["Region"]
    identitypool = event["ResourceProperties"]["IdentityPool"]
    encryptionKey = event["ResourceProperties"]["EncryptionKey"]
    appId = event["ResourceProperties"]["AppId"]
    branchName = event["ResourceProperties"]["BranchName"]
    userPoolId = event["ResourceProperties"]["UserPoolId"]
    clientId = event["ResourceProperties"]["ClientId"]
    
    if event['RequestType'] == 'Create':
        try:
            make_directories()
            copy_website_to_tmp()
            write_variables_file(bucket, region, identitypool, encryptionKey, userPoolId, clientId)
            zip_website_contents()
            url = deploy_site_to_amplify(appId, branchName)
        except Exception as e:
            print("An exception occurred: {}".format(e))
            cfnresponse.send(event, context, cfnresponse.FAILED)
        
        #Return the URL to the stack
        responseData = {}
        responseData["WebsiteUrl"] = url
        cfnresponse.send(event, context, cfnresponse.SUCCESS, responseData)
    else:
        cfnresponse.send(event, context, cfnresponse.SUCCESS, {})
    
#Deploy the site to Amplify, returning the URL to the deployed site
def deploy_site_to_amplify(appId, branchName):
    client = boto3.client('amplify')
    
    #Get the upload URL for the zip artifact
    response = client.create_deployment(appId=appId, branchName=branchName)
    zipUrl = response['zipUploadUrl']
    jobId = response['jobId']
    
    #Now upload the artificat to the pre-signed URL
    with open(ZIP_FILE_NAME, 'rb') as file:
        obj_text = file.read()
        
    response = http.request('PUT', zipUrl, body=obj_text)
    
    #Now start the deployment, passing in the jobId from the previous call
    client.start_deployment(appId=appId, branchName=branchName, jobId=jobId)
    
    #Now get the URL and return it
    response = client.get_app(appId=appId)
    appUrl = "https://website.{}".format(response['app']['defaultDomain'])
    
    return appUrl
    
#Make directories necessary for the deployment process
def make_directories():
    if not os.path.exists("/tmp/website"):
        os.mkdir("/tmp/website")
        
    if not os.path.exists("/tmp/website/images"):
        os.mkdir("/tmp/website/images")
        
    if not os.path.exists('/tmp/website/scripts'):
        os.mkdir("/tmp/website/scripts")
        
#Write the variables file to be used by the website
def write_variables_file(bucket_name, bucket_region, identity_pool_id, encryption_key_id, user_pool_id, client_id):
    f = open('/tmp/website/scripts/variables.js', 'w')
    f.write('var bucketName = "{}";\n'.format(bucket_name))
    f.write('var bucketRegion = "{}";\n'.format(bucket_region))
    f.write('var IdentityPoolId = "{}";\n'.format(identity_pool_id))
    f.write('var EncryptionKeyId = "{}";\n'.format(encryption_key_id))
    f.write('var userPoolId = "{}";\n'.format(user_pool_id))
    f.write('var clientId = "{}";\n'.format(client_id))
    f.close()

#Copy the website contents to the tmp directory for deployment
def copy_website_to_tmp():
    file_paths = get_all_file_paths("website")
    
    for file in file_paths:
        os.system('cp {} /tmp/{}'.format(file, file))
    
#Zip the website contents 
def zip_website_contents():
    #Change the working directory so we don't end up with the top-level folder of the ZIP file be /tmp
    os.chdir('/tmp/website')
    
    file_paths = get_all_file_paths(".")
    
    with ZipFile(ZIP_FILE_NAME, 'w') as zip:
        for file in file_paths:
            file = file.replace("./", "")
            zip.write(file)

def get_all_file_paths(directory):
    # initializing empty file paths list
    file_paths = []
  
    # crawling through directory and subdirectories
    for root, directories, files in os.walk(directory):
        for filename in files:
            # join the two strings in order to form the full filepath.
            filepath = os.path.join(root, filename)
            file_paths.append(filepath)
  
    # returning all file paths
    return file_paths