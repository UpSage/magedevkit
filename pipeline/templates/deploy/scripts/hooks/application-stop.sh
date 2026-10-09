#!/usr/bin/env bash
# @@HEADER@@
#
# ApplicationStop — deliberately does nothing: the site keeps serving while
# a new release is installed (deploy/scripts/deploy switches maintenance on
# only around a needed setup:upgrade).

exit 0
