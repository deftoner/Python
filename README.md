# Python
Collection of tools written in Python

[FreshDeskTicketDB.py](FreshDeskTicketDB.py) A simple but working tool to download all your FreshDesk tickets so you have a local backup. Edit the file to add the domain (normally companyname.freshdesk.com), agents (useful when your API does not have admin access, knowing the agent names will convert "user123456" into agents), leave it blank if you have admin right. And of course, the API Key

python FreshDeskTicketDB.py --month 2024-01                   # Download one month    
python FreshDeskTicketDB.py --month 2024-01 --month 2024-02   # multiple months
python FreshDeskTicketDB.py --year 2024                       # ALl year (Note: does not work adding more than one year)
python FreshDeskTicketDB.py --from 2024-01-01 --to 2024-03-31 # Dates range
python FreshDeskTicketDB.py --days 30                         # Latest X days
python FreshDeskTicketDB.py --ticket 12345                    # Specific ticket number
python FreshDeskTicketDB.py --force                           # re-download already saved tickets
python FreshDeskTicketDB.py --output MyDB/                    # custom folder
