## RAG Chatbot

This sample uses the retrieval agumented generation (RAG) pattern to create a chatbot that can be used for a variety of purposes. 

### Architecture

The architecture of the solution is as follows:

![RAG architecture](images/architecture.png)

1. A user sends a request off to a chatbot
2. The Lex chatbot receives the request and sends it off to a Lambda function. The Lambda function sends the user's query to Amazon Bedrock. It asks Amazon Bedrock to answer the user's question, using only the context it finds in its knowledge base. 
3. Amazon Bedrock returns a response, which AWS Lambda sends back to the Lex chatbot
4. The Lex chatbot receives the results from the Lambda function and sends it on to the user to answer their question

### Steps to deploy

1. The chatbot requires a Lambda layer for OpenSearch\. Create a layer as follows:

```
mkdir -p package/python
pip install opensearch-py -t package/
cd package
zip -r ../opensearch-layer.zip .
```

Upload the layer to the Lambda console under *Layers*. Note the ARN and use it in the next step.

2. Deploy the [CloudFormation template](rag-with-KBforBedrock.yml). Add the Lambda Layer ARN to the **OpenSearchVersionArn** parameter. The deployment will take about 10 minutes to complete
3. Once complete, click on the CloudFormation **Outputs** tab. The value for the **S3Bucket** key is the S3 bucket where your content should be added. 
4. Go to the S3 console, find the bucket, and place your content in this bucket. 
5. Go to the Amazon Bedrock console. Find the Knowledge Base that was created (it should be named {*stack-name*}-KB, where *stack-name* is the name of your CloudFormation stack). Click the knowledge base name. In the page that appears, click on **Sync** button in the **Data source** section. Wait a few minutes while the data is syncing. 
6. Go to the Lex console and click the link for the Bot (it should be named *{rag-chatbot}*-bot,where *{rag-chatbot}* is the name of your CloudFormation stack). 
7. From the left naviation, select **Intents**. It is available under **Bot versions** -> **Draft version** -> **All languages** -> **English (US)** -> **Intents**
8. In the **Intents** screen, click the **Build** button. Wait for the Lex bot to be built, then click **Test** to test the bot. 
