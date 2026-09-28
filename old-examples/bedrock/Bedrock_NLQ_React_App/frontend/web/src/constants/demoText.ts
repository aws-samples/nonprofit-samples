export const APP_DESCRIPTION: string = `
  We use Amazon Bedrock to generate SQL queries from natural language 
  and return the results conversationally. You can ask questions 
  about campaigns, donations, and donors from the sample data in this demo.
`;

export const DATASET_ITEMS: { title: string; description: string }[] = [
  { title: "Donations", description: "Transactions including donation amount across various fundraising events." },
  { title: "Donors", description: "Details from donor profiles including email, phone, and address." },
  { title: "Events", description: "Details about fundraising events including name, date, location, and type." },
  { title: "Campaigns", description: "Details about fundraising campaigns including start date, end date, and target amount." },
  { title: "Dates", description: "Lookup table for campaign and event dates." },
  { title: "Payment Method", description: "Details about donor payment methods." }
];