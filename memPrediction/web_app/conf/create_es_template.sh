#!/bin/bash

TEMPLATES="job
summary
"
ESURL="{ESURL}"
USER="{USER}"
PASS="{PASS}"
CERT="{CERT}"

function del_template() {
    for template in $TEMPLATES
    do
        echo "del template $template"
        curl -u $USER:$PASS --cacert $CERT -XDELETE $ESURL/_index_template/$template
    done
}

function add_template() {
    for template in $TEMPLATES
    do
        echo "add template $template"
        curl -u $USER:$PASS --cacert $CERT  -XPUT $ESURL/_index_template/$template -H 'Content-Type: application/json'  --data-binary @${template}.json
    done
}

function del_index() {
    for template in $TEMPLATES
    do
        echo "del index $template"
        curl -u $USER:$PASS --cacert $CERT -XDELETE $ESURL/${template}-*
    done
}

#del_index
#del_template
add_template