# Python
Collection of tools written in Python

[FreshDeskTicketDB.py](FreshDeskTicketDB.py) A simple but working tool to download all your FreshDesk tickets so you have a local backup. Edit the file to add the domain (normally companyname.freshdesk.com), agents (useful when your API does not have admin access, knowing the agent names will convert "user123456" into agents), leave it blank if you have admin right. And of course, the API Key  

```python
python FreshDeskTicketDB.py --month 2024-01                   # Download one month  
python FreshDeskTicketDB.py --month 2024-01 --month 2024-02   # multiple months  
python FreshDeskTicketDB.py --year 2024                       # ALl year (Note: does not work adding more than one year)  
python FreshDeskTicketDB.py --from 2024-01-01 --to 2024-03-31 # Dates range  
python FreshDeskTicketDB.py --days 30                         # Latest X days  
python FreshDeskTicketDB.py --ticket 12345                    # Specific ticket number  
python FreshDeskTicketDB.py --force                           # re-download already saved tickets  
python FreshDeskTicketDB.py --output MyDB/                    # custom folder  
```  
[FreshDeskTicketSearch.py](FreshDeskTicketSearch.py) A simple tool to search the downloaded ticket database (specially usefull since freshdesk online search is really bad).   

```python
python FreshDeskTicketSearch.py --customer "John Smith"            # Search by customer ("from")  
python FreshDeskTicketSearch.py --subject "email alert"            # Search but ticket subject  
python FreshDeskTicketSearch.py --search "smtp timeout"            # Global text search, this will show results from subject, and content  
python FreshDeskTicketSearch.py --version 6.1                      # Search by Version  
python FreshDeskTicketSearch.py --status resolved                  # Status: Open, Close, Resolved, etc  
python FreshDeskTicketSearch.py --month 2024-01                    # Specific month  
python FreshDeskTicketSearch.py --from 2024-01-01 --to 2024-03-31  # Date range  
python FreshDeskTicketSearch.py --ticket 12345                     # Ticket number  
python FreshDeskTicketSearch.py --customer "Acme" --search "agent" # AND logic  
python FreshDeskTicketSearch.py --search "syslog" --show-full      # full file  
```
