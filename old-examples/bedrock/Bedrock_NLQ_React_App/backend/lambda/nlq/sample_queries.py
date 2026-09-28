sample_queries = """
Example SQL Queries:
1. Query: What was the total donation amount for the March Miracle Makers campaign??

    SELECT SUM(d.donationamount) AS total_donation_amount 
    FROM sample_donations d 
    JOIN sample_campaigns c ON d.campaignkey = c.campaignkey 
    WHERE LOWER(c.campaignname) LIKE '%march miracle makers%'
     
   Expected Result:
   | total_donation_amount |
   |-----------------------|
   | 9855                  | 

"""
