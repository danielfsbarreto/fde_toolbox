# fish completions for bin/deploy (FDE Activity Log): its options. fish completes bin/deploy by its name, deploy, so
# every rule checks it is this repository's script. Loaded at fish startup with the other files in bin/completions/.

function __deploy_root
    set -l script (path resolve -- (commandline -opc)[1])
    test -f "$script"; or return 1
    test -d (path dirname -- (path dirname -- $script))/flows
end

# The flows bin/deploy knows (its FLOWS table), with whether each has a deployment yet (for --flow)
function __deploy_flows
    set -l script (path resolve -- (commandline -opc)[1])
    string replace -rf '^\s+"(\w+) (-|[0-9a-f-]{36})"\s*(?:#\s*)?(.*)$' '$1\t$3' <$script
end

complete -c deploy -n __deploy_root -f
complete -c deploy -n __deploy_root -l all -d 'Everything: the flows to AMP and the review app to Heroku'
complete -c deploy -n __deploy_root -l amp -d 'Only the flows, to AMP'
complete -c deploy -n __deploy_root -l heroku -d 'Only the review app, to Heroku'
complete -c deploy -n __deploy_root -l dry-run -d 'Checks only, deploy nothing'
complete -c deploy -n __deploy_root -l force -d "Heroku: replace its history (after amending a deployed commit)"
complete -c deploy -n __deploy_root -l no-check -d 'Heroku: skip the local Docker build'
complete -c deploy -n __deploy_root -l no-validate -d 'AMP: skip crewai deploy validate'
complete -c deploy -n __deploy_root -l status-only -d 'AMP: only follow the status, deploy nothing'
complete -c deploy -n __deploy_root -l every -x -d 'AMP: seconds between status checks'
complete -c deploy -n __deploy_root -l timeout -x -d 'AMP: seconds before giving up following'
complete -c deploy -n __deploy_root -l flow -x -a '(__deploy_flows)' -d 'AMP: only this flow (repeatable)'
