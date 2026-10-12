from curl_cffi import  requests


headers = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "content-type": "application/json",
    "origin": "https://orion.nv.gov",
    "priority": "u=1, i",
    "referer": "https://orion.nv.gov/portal/public/",
    "sec-ch-ua": "\"Chromium\";v=\"154\", \"Google Chrome\";v=\"154\", \"Not A(Brand\";v=\"99\"",
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": "\"Windows\"",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36"
}
cookies = {
    "visid_incap_3334702": "TsqMdmaoTtmzfPq1LUXon9dWzGoAAAAAQUIPAAAAAABiLrWUp72E3/h7+3rZebDr",
    "nlbi_3334702": "CayhDg/im2xqFhBfRLpTGAAAAABTa9FBq23lGMdrBXr6c6st",
    "incap_ses_1137_3334702": "V4lQeSwsYWiByGzUKnDHD9dWzGoAAAAAXLe7ZHZj4DUrUJPcqb0qZQ==",
    "nlbi_3334702_2147483392": "0d44OAzf5h+Pb5QiRLpTGAAAAAB5NBd5SlgkQllHqLjaKgL5",
    "reese84": "3:nKKZlzce0fvYFn9TvA6jNw==:gNL4mfo4Nf2t154qwZH4qwvPh3DJb3Cez0g1enjbXFVt63/91dOPeGA9gGZRW8eJHjNP/BWOEMexPQD3h1BRp6Gh2SlQ0Hs8cNTcSz1a4Fox46mHi1CDsCQnhZeXz2DhuXk11cHyOi0XD0XJtLW4erVS8NUeWcbK+KzF1yDnUU0=:PMURpLEkQk5dtbAoCaLzbYqPbA/culzD0NFF44OJ2GU=:eUnVwe8Hext2/nWsEJ1cu4of+QtfSn05robJHoasHXh0NcWO6vwYe/2YfsnTGIVH9BBquAIEW+LmHpbp5V9L+jnUEGpFIcq4w9BxPVSWnAtx3fzXx/7ewJGkv2Sr4j2LM8zJ5H+uQs+OK+cNfJJ7VzGZwv4jY9L34EYglVHbQJLdO/hwwOKRWI85mn7r52p0aRIo9OAFpidR4ia/x9F9eNFDzmBTmFA2twqNQzZXFi94LvP7uy5V/79f9TwavvSRdfFXH/YSnhnJvWPaihtI5y3UvT1HW6nNm66l6CIunboPxv+WZkoPsYsqWW9DIIAn6ip0R1e747VvF2jy9Ndv1vQO38DcVT9azpsHdiRzHE7apk+ojnbGXg==:",
    "sessionId": "e6c039b3-e4b2-41a8-af97-bd8269ecc3d8",
    "fp_token_7c6a6574-f011-4c9a-abdd-9894a102ccef": "88N8j8iDDplo8c6N77SuT0BN6Esp0SMomRm0r1G/wqU="
}
url = 'https://orion.nv.gov/core/api/_q/v1/nv-sos/public/CRAEntities'
data = {
    "data": {},
    "origin": "",
    "origin_key": "CaseX",
    "gridInput": {
        "params": {
            "parameters": {
                "agentName": "NORTHWEST",
                "registeredAgentType": "",
                "craId": "",
                "nvBusinessId": "",
                "craEntityNumber": "",
                "agentStatus": "",
                "filingNo": "",
                "submit1": False,
                "searchDumy": True,
                "restrictedAndProhibitedWords": "",
                "totalRecordCount": "",
                "searchType": "STARTS_WITH",
                "searchTerm": ""
            }
        },
        "persistPrevParams": False
    },
    "parameters": {
        "agentName": "NORTHWEST",
        "registeredAgentType": "",
        "craId": "",
        "nvBusinessId": "",
        "craEntityNumber": "",
        "agentStatus": "",
        "filingNo": "",
        "submit1": False,
        "searchDumy": True,
        "restrictedAndProhibitedWords": "",
        "totalRecordCount": "",
        "searchType": "STARTS_WITH",
        "searchTerm": ""
    },
    "pageNo": 1,
    "pageSize": 25,
    "sortBy": {}
}
response = requests.post(url, headers=headers, cookies=cookies, json=data)

print(response.text)
print(response)