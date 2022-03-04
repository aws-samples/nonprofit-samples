import sys

EMAIL_ADDRESS = sys.argv[1]

emailpart = EMAIL_ADDRESS.split("@")

f = open("pinpoint-users.csv", "w")
f.write("ChannelType,Address,Location.Country,Demographic.Platform,Demographic.Make,User.UserId,User.UserAttributes.FirstName\n")

f.write("EMAIL," + emailpart[0] + '+user_1@' + emailpart[1] + ",US,iOS,Apple,user_1,Alejandro\n")
f.write("EMAIL," + emailpart[0] + '+user_2@' + emailpart[1] + ",US,Android,LG,user_2,Akua\n")
f.write("EMAIL," + emailpart[0] + '+user_3@' + emailpart[1] + ",US,iOS,Apple,user_3,Martha\n")
f.write("EMAIL," + emailpart[0] + '+user_14@' + emailpart[1] + ",US,iOS,Apple,user_14,Mateo\n")
f.write("EMAIL," + emailpart[0] + '+user_5@' + emailpart[1] + ",US,Android,Google,user_5,Zhang\n")

f.close()