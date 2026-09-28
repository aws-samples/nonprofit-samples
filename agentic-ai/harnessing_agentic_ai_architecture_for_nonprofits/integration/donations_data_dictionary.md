# Donations Database Data Dictionary

## Database Description
This database is used for managing the Donation application data, including Donation transactions that are stored in a central fact table with auxillary information like donors, events, campaigns and payment method stored in other tables, plus other related entities.

## Tables

### Donation
Table storing donation information

| Column Name | Data Type | Constraints | Description |
|------------|-----------|-------------|-------------|
| id | BIGINT | PRIMARY KEY, IDENTITY | The unique identifier for this record |
| donationamount | DECIMAL(19,4) | | The amount of the donation in dollars |
| donor | BIGINT | FOREIGN KEY | Reference to donor table |
| campaign | BIGINT | FOREIGN KEY | Reference to campaign table |
| datekey | BIGINT | FOREIGN KEY | Reference to datekey table |
| paymentmethod | BIGINT | FOREIGN KEY | Reference to paymentmethod table |
| event | BIGINT | FOREIGN KEY | Reference to eventkey table |

### Donor
Table storing donor information

| Column Name | Data Type | Constraints | Description |
|------------|-----------|-------------|-------------|
| id | BIGINT | PRIMARY KEY, IDENTITY | The unique identifier for this record |
| firstname | VARCHAR(255) | NOT NULL | The first name associated with this entity |
| lastname | VARCHAR(255) | NOT NULL | The last name associated with this entity |
| email | VARCHAR(255) | NOT NULL | The email associated with this entity |
| phone | VARCHAR(255) | NOT NULL | The phone number associated with this entity |
| address | VARCHAR(255) | NOT NULL | The address associated with this entity |
| joindate | TIMESTAMP | | The date the donor joined the organization |
| donorstatus | VARCHAR(255) | | The current state of the entity (e.g., current, past, prospect) |

### Campaign
Table storing campaign information

| Column Name | Data Type | Constraints | Description |
|------------|-----------|-------------|-------------|
| id | BIGINT | PRIMARY KEY, IDENTITY | The unique identifier for this record |
| campaignname | VARCHAR(255) | | The name of the campaign |
| startdate | TIMESTAMP | | The date the campaign started |
| enddate | TIMESTAMP | | The date the campaign ends |
| campaigntype | VARCHAR(255) | | The type of campaign (e.g. go-fund-me, outreach) |
| targetamount | DECIMAL(19,4) | | The target amount of the campaign in dollars |

### DateKey
Table storing the date of the donation information

| Column Name | Data Type | Constraints | Description |
|------------|-----------|-------------|-------------|
| id | BIGINT | PRIMARY KEY, IDENTITY | The unique identifier for this address record |
| day | TINYINT | NOT NULL | The day of the date |
| month | TINYINT | NOT NULL | The month of the date |
| year | SMALLINT | NOT NULL | The year of the date |
| quarter | TINYINT | NOT NULL | The quarter of the date |
| isholiday | BOOLEAN | | Indicator if the date is a holiday (true/false) |

### Event
Table storing event information

| Column Name | Data Type | Constraints | Description |
|------------|-----------|-------------|-------------|
| id | BIGINT | PRIMARY KEY, IDENTITY | The unique identifier for this record |
| eventname | VARCHAR(255) | | The name of the event |
| eventdate | TIMESTAMP | | The date of the event |
| eventlocation | VARCHAR(255) | | The location of the event |
| eventtype | VARCHAR(255) | | The type of event (e.g., fundraiser, outreach) |

### PaymentMethod
Table storing payment method information

| Column Name | Data Type | Constraints | Description |
|------------|-----------|-------------|-------------|
| id | BIGINT | PRIMARY KEY, IDENTITY | The unique identifier for this record |
| paymentmethodname | VARCHAR(255) | | The name of the payment method |
