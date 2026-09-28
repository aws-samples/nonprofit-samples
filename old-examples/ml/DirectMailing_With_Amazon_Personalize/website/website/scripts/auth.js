var cognitoUser;
var idToken;
var userPool;
var sessionUserAttributes;

var poolData = {
    UserPoolId: userPoolId,
    ClientId: clientId
};

userPool = new AmazonCognitoIdentity.CognitoUserPool(poolData);
cognitoUser = userPool.getCurrentUser();

function logIn(){
    if(!$('#userNameInput').val() || !$('#passwordInput').val()){
        alert('Please enter Username and Password!');
    }else{
        var authenticationData = {
            Username : $('#userNameInput').val(),
            Password : $("#passwordInput").val(),
        };
        var authenticationDetails = new AmazonCognitoIdentity.AuthenticationDetails(authenticationData);

        var userData = {
            Username : $('#userNameInput').val(),
            Pool : userPool
        };
        cognitoUser = new AmazonCognitoIdentity.CognitoUser(userData);

        $("#loader").show();
        cognitoUser.authenticateUser(authenticationDetails, {
            onSuccess: function (result) {
                //switchToLoggedInView();
                window.location.href="index.html"

                idToken = result.getIdToken().getJwtToken();
                getCognitoIdentityCredentials();
            },

            onFailure: function(err) {
                alert(err.message);
                $("#loader").hide();
            },
            
            newPasswordRequired: function(userAttributes, requiredAttributes) {
                delete userAttributes.email_verified;
                sessionUserAttributes = userAttributes;
                $("#login").hide();
                $("#resetpassword").show();
            }

        });
    }
}

function resetPassword(){
    var newpassword1 = $('#newPasswordInput1').val();
    var newpassword2 = $('#newPasswordInput2').val();
    
    if (newpassword1 != newpassword2)
        alert("Passwords do not match");
    else
        handleNewPassword(newpassword1);
}

function handleNewPassword(newPassword) {    
        
    var authenticationData = {
        Username : $('#userNameInput').val(),
        Password : $("#passwordInput").val(),
    };
    var authenticationDetails = new AmazonCognitoIdentity.AuthenticationDetails(authenticationData);

    var userData = {
        Username : $('#userNameInput').val(),
        Pool : userPool
    };
    cognitoUser = new AmazonCognitoIdentity.CognitoUser(userData);
    
    cognitoUser.authenticateUser(authenticationDetails, {
        onSuccess: function (result) {
            //switchToLoggedInView();
            window.location.href="index.html"

            idToken = result.getIdToken().getJwtToken();
            getCognitoIdentityCredentials();
        },

        onFailure: function(err) {
            alert(err.message);
            $("#loader").hide();
        },
        
        newPasswordRequired: function(userAttributes, requiredAttributes) {
            delete userAttributes.email_verified;
            delete userAttributes.email
            sessionUserAttributes = userAttributes;
            
            cognitoUser.completeNewPasswordChallenge(newPassword, sessionUserAttributes, this, {
                onSuccess: function() {
                 //redirect
                 window.location.href="index.html"
                },
                onFailure: function(err) {
                 alert(err.message);
                }
             });
        }

    });   
}

function getCurrentLoggedInSession(){

    $("#loader").show();
    userPool = new AmazonCognitoIdentity.CognitoUserPool(poolData);
    cognitoUser = userPool.getCurrentUser();
    
    if(cognitoUser != null){
        cognitoUser.getSession(function(err, session) {
            if (err) {
                alert(err.message);
            }else{
                idToken = session.getIdToken().getJwtToken();
                getCognitoIdentityCredentials();
            }
            $("#loader").hide();
        });
    }else{
        alert('Session expired. Please log in again.');
        $("#loader").hide();
        window.location.href="login.html"
    }

}

function getCognitoIdentityCredentials(){
    AWS.config.region = bucketRegion;

    var loginMap = {};
    loginMap['cognito-idp.' + bucketRegion + '.amazonaws.com/' + userPoolId] = idToken;

    AWS.config.credentials = new AWS.CognitoIdentityCredentials({
        IdentityPoolId: IdentityPoolId,
        Logins: loginMap
    });

    AWS.config.credentials.clearCachedId();

    AWS.config.credentials.get(function(err) {
        if (err){
            logMessage(err.message);
        }
    });
}

function logMessage(message){
    $('#log').append(message + '</br>');
}