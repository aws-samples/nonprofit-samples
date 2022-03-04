import random
import datetime
import time 

event_types = ['DONATE', 'EVENTS', 'GET_INVOLVED', 'NEWS', 'MEMBER_BENEFITS']

start_date = datetime.date(2021, 1, 1)
end_date = datetime.date(2021, 10, 31)

f = open("UserInteractions.csv", "w")
f.write("USER_ID,ITEM_ID,TIMESTAMP\n")
for user in range(30):
    username = "user_{}".format(user)
    default_choice = random.choice(event_types)
    
    for i in range(100):
        rand_num = random.random()

        if rand_num > 0.5:
            event = default_choice
        else:
            event = random.choice(event_types)

        time_between_dates = end_date - start_date
        days_between_dates = time_between_dates.days
        random_number_of_days = random.randrange(days_between_dates)
        random_date = start_date + datetime.timedelta(days=random_number_of_days)
        curr_time = time.mktime(random_date.timetuple())

        f.write("{},{},{}\n".format(username, event, str(int(curr_time))))

f.close()
