"""Fake credentials for the tests — one place, built at run time.

They have the shape MageDevKit's checks look for (an access key ID, a
40-character secret, a GitHub token, …) but are not real anywhere. Each is
assembled from pieces, so no literal in the source matches a secret
scanner's pattern: GitHub flags a whole AWS key or token written out in a
file, even a fake one. tests/test_pipeline.py's SecretScan makes sure the
repo stays that way.
"""

_AKIA = "AK" + "IA"

AWS_KEY = _AKIA + "TESTFAKE" + "KEY00001"          # a MageDevKit connection's key
AWS_KEY_SHOP = _AKIA + "TESTFAKE" + "SHOP0001"     # a store's own S3 profile in ~/.aws
AWS_KEY_STRAY = _AKIA + "TESTFAKE" + "STRAY001"    # exported in the shell — must never be used
AWS_KEY_MEDIA = _AKIA + "TESTFAKE" + "MEDIA001"    # read back for S3 media
AWS_TEMP_KEY = "AS" + "IA" + "TESTFAKE" + "TEMP0001"
AWS_SECRET = "fakeSecret" + "0" * 26 + "/+ab"      # 40 characters
AWS_SESSION_TOKEN = "Fake" + "Session" + "Token" + "0" * 12
GITHUB_TOKEN = "gh" + "p_" + "fake" * 9            # 36 characters after the prefix
PRIVATE_KEY_HEADER = "-----BEGIN RSA " + "PRIVATE KEY-----"
CRYPT_KEY = "fake" + "crypt" + "0" * 23            # 32 characters
MAGENTO_REPO_KEY = "0" * 16 + "f" * 16             # 32 hex characters
