# NUR 3302 Week 1 Day 2 Streamlit Escape Room + Faculty Dashboard

This package contains the student escape room and a password-protected faculty dashboard in the same Streamlit app.

## Files
- `app.py` — student escape room + faculty dashboard
- `requirements.txt` — Python dependencies
- `.streamlit/secrets.toml.example` — example configuration for the faculty password and Google Sheets backend

## What the faculty dashboard shows
The dashboard automatically summarizes completed student/team attempts and includes:
- total submissions
- average score
- average actual completion time
- average hints used
- average incorrect attempts
- score range and median completion time
- student/team detail table
- filters for section, individual/team mode, and student/team name
- average room completion time
- average incorrect attempts by room
- percentage of students/teams using a hint in each room
- identification of the room with the most errors
- identification of the room taking the longest
- downloadable class CSV
- password-protected instructor answer key

## Student results collected
Each completed attempt records:
- unique submission ID
- completion date/time
- individual or team mode
- student name(s)
- course section
- team name
- total score
- accuracy score
- efficiency score
- actual total time
- adjusted total time
- total hints
- total incorrect attempts
- time in each of the five rooms
- incorrect attempts in each room
- hints used in each room
- final-word attempts
- final-word hint use

## Why Google Sheets is used
Streamlit browser sessions are separate and Streamlit's local file storage is not reliable as a permanent class database. A Google Sheet gives the app one shared location for all student results.

Students do not need a Google account and do not receive access to the Google Sheet. The Streamlit app writes results through a Google service account.

# One-time Google Sheets setup

## 1. Create the Google Sheet
1. Open Google Sheets.
2. Create a blank spreadsheet.
3. Give it a name such as `NUT 3302 Escape Room Results`.
4. You do not need to create column headings. The app creates the `Results` tab and headings automatically.
5. Copy the spreadsheet ID from the URL. It is the long set of characters between `/d/` and `/edit`.

## 2. Create a Google Cloud service account
1. Go to Google Cloud Console.
2. Create a project or select an existing project.
3. Enable the **Google Sheets API**.
4. Enable the **Google Drive API**.
5. Go to **IAM & Admin > Service Accounts**.
6. Create a service account for the Streamlit app.
7. Create a JSON key for that service account and download it.

The JSON file contains fields such as `project_id`, `private_key`, and `client_email`.

## 3. Share the Google Sheet with the service account
1. Copy `client_email` from the service-account JSON file.
2. Open the Google Sheet.
3. Click **Share**.
4. Add the service-account email as an **Editor**.

This step allows the Streamlit app to write student results to the sheet.

# Streamlit deployment

## 4. Upload the app to GitHub
Upload these items to the GitHub repository:
- `app.py`
- `requirements.txt`

You may also upload this README.

Do not upload a real `secrets.toml` file or the downloaded Google JSON key to a public repository.

## 5. Deploy the app
1. Open Streamlit Community Cloud.
2. Choose **Create app**.
3. Select the GitHub repository and branch.
4. Set the main file path to `app.py`.
5. Deploy the app.

## 6. Add Streamlit Secrets
In Streamlit Community Cloud:
1. Open the app settings.
2. Open **Secrets**.
3. Use `.streamlit/secrets.toml.example` as the structure.
4. Set your own faculty password.
5. Paste the Google Sheet ID into `spreadsheet_id`.
6. Copy the values from the Google service-account JSON into `[google_service_account]`.
7. Save the secrets.
8. Reboot the app if Streamlit does not reboot automatically.

Important: The private key must keep `\n` characters inside the quoted value, as shown in the example secrets file.

# Using the app

## Students
Students leave the sidebar set to **Student Escape Room**. They enter their name(s), section, and individual/team mode, then complete the activity.

When they escape:
- their result automatically saves to the shared Google Sheet
- they see their score and room-by-room performance
- they can still download their own CSV as a backup

## Faculty
1. Open the same Streamlit link.
2. In the sidebar, change **View** to **Faculty Dashboard**.
3. Enter the faculty password stored in Streamlit Secrets.
4. Filter and review class results.
5. Download filtered results when needed.

# Scoring
Accuracy begins at 80 points.

Each incorrect room/final submission deducts 2 points.

Each hint:
- deducts 5 points
- adds 2 minutes to adjusted completion time

Efficiency contributes up to 20 points:
- 30 minutes or less: 20 points
- 31–35 minutes: 17 points
- 36–40 minutes: 14 points
- 41–45 minutes: 10 points
- 46–50 minutes: 6 points
- over 50 minutes: 3 points

Maximum score: 100.

# Backup mode
If Google Sheets is not configured, students can still complete the escape room and download individual result CSV files. The faculty dashboard also provides an upload option for reviewing CSVs manually.
