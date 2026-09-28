
AWS.config.update({
  region: bucketRegion,
  credentials: new AWS.CognitoIdentityCredentials({
    IdentityPoolId: IdentityPoolId
  })
});

var s3 = new AWS.S3({
  apiVersion: "2006-03-01",
  params: { Bucket: bucketName }
});

function listAlbums() {    
    template =  '<input id="photoupload" type="file" accept=".xlsx"><button id="addphoto" onclick="addExcel()">Add Excel Doc</button>'
    document.getElementById("app").innerHTML = template;
  }

function addExcel() {
  var files = document.getElementById("photoupload").files;
  if (!files.length) {
    return alert("Please choose a file to upload first.");
  }
  var file = files[0];
  var fileName = file.name;
  var today = new Date().toISOString().slice(0, 10)
  var prefix = "ingest/" + today;
  var excelKey = prefix + "/" + fileName;

  // Use S3 ManagedUpload class as it supports multipart uploads
  var upload = new AWS.S3.ManagedUpload({
    params: {
      Bucket: bucketName,
      Key: excelKey,
      Body: file,
      ServerSideEncryption:"aws:kms", 
      SSEKMSKeyId: EncryptionKeyId
    }
  });

  var promise = upload.promise();

  promise.then(
    function(data) {
      alert("Successfully uploaded Excel document. Please wait for personalization recommendations to complete.");
    },
    function(err) {
      return alert("There was an error uploading your Excel document: ", err.message);
    }
  );
}