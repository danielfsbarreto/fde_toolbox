# fish completions for bin/flow (FDE Activity Log): the flow, its command, and that command's arguments.
#
# Loaded at fish startup with the other files in bin/completions/ (see the README's setup); fish completes bin/flow
# by its name, flow.
#
# Everything is read from the repository: new flows (flows/<name>) and their [project.scripts] show up by themselves.

# The repository root of the bin/flow being completed; empty for any other `flow` command
function __flow_root
    set -l command (commandline -opc)[1]
    set -l script (path resolve -- $command)
    test -f "$script"; or set script (command -s -- $command)
    test -n "$script"; or return 1
    set -l root (path dirname -- (path dirname -- $script))
    test -d "$root/flows"; and echo $root
end

# The words typed after bin/flow, without the one being completed
function __flow_words
    set -l words (commandline -opc)
    set -e words[1]
    test (count $words) -gt 0; and printf '%s\n' $words
end

function __flow_names
    set -l root (__flow_root); or return
    for project in $root/flows/*/pyproject.toml
        path basename -- (path dirname -- $project)
    end
end

# The flow's own commands: the [project.scripts] of its pyproject.toml
function __flow_scripts --argument-names name
    set -l root (__flow_root); or return
    set -l pyproject $root/flows/$name/pyproject.toml
    test -f $pyproject; or return
    set -l inside 0
    while read -l line
        if string match -qr '^\[project\.scripts\]' -- $line
            set inside 1
        else if string match -qr '^\[' -- $line
            set inside 0
        else if test $inside = 1; and string match -qr '^\s*[\w-]+\s*=' -- $line
            string replace -r '^\s*([\w-]+)\s*=.*' '$1' -- $line
        end
    end <$pyproject
end

# kickoff inputs: yesterday and the last 10 weekdays, as the JSON the flow takes
function __flow_dates
    echo '\'{"date": "yesterday"}\''\t'the day before today'
    set -l found 0
    for back in (seq 1 16)
        set -l day (date -v-{$back}d +%F 2>/dev/null; or date -d "-$back days" +%F 2>/dev/null)
        set -l weekday (date -j -f %F $day +%u 2>/dev/null; or date -d $day +%u 2>/dev/null)
        test "$weekday" -ge 6; and continue
        set -l name (date -j -f %F $day +%A 2>/dev/null; or date -d $day +%A 2>/dev/null)
        echo '\'{"date": "'$day'"}\''\t$name
        set found (math $found + 1)
        test $found -ge 10; and break
    end
end

# The deployment UUIDs bin/deploy knows (for crewai deploy ... -u, and bin/deploy --uuid)
function __flow_uuids
    set -l root (__flow_root); or return
    string replace -rf '^\s+"\w+ ([0-9a-f-]{36})"\s*(?:#\s*)?(.*)$' '$1\t$2' <$root/bin/deploy
end

function __flow_is --argument-names position value
    set -l words (__flow_words)
    test (count $words) -ge $position; and test "$words[$position]" = "$value"
end

function __flow_count --argument-names expected
    test (count (__flow_words)) -eq $expected
end

complete -c flow -f
complete -c flow -n '__flow_root >/dev/null; and __flow_count 0' -a '(__flow_names)' -d Flow
complete -c flow -n '__flow_root >/dev/null; and __flow_count 0' -s h -l help -d 'Show examples'

# The command, once a flow is chosen
complete -c flow -n '__flow_root >/dev/null; and __flow_count 1' -a '(__flow_scripts (__flow_words)[1])' -d 'Flow script'
complete -c flow -n '__flow_root >/dev/null; and __flow_count 1' -a sync -d 'Install its dependencies (uv sync)'
complete -c flow -n '__flow_root >/dev/null; and __flow_count 1' -a lock -d 'Update its uv.lock (uv lock)'
complete -c flow -n '__flow_root >/dev/null; and __flow_count 1' -a crewai -d 'CrewAI CLI inside the flow'
complete -c flow -n '__flow_root >/dev/null; and __flow_count 1' -a python -d 'Python with the flow environment'

# kickoff: the day to run
complete -c flow -k -n '__flow_root >/dev/null; and __flow_count 2; and __flow_is 2 kickoff' -a '(__flow_dates)'

# crewai: the subcommands used here
complete -c flow -n '__flow_root >/dev/null; and __flow_count 2; and __flow_is 2 crewai' -a run -d 'Run the flow locally'
complete -c flow -n '__flow_root >/dev/null; and __flow_count 2; and __flow_is 2 crewai' -a eval -d 'Grade the last traced run'
complete -c flow -n '__flow_root >/dev/null; and __flow_count 2; and __flow_is 2 crewai' -a flow -d 'Flow commands (plot)'
complete -c flow -n '__flow_root >/dev/null; and __flow_count 2; and __flow_is 2 crewai' -a deploy -d 'Deployment commands'
complete -c flow -n '__flow_root >/dev/null; and __flow_count 2; and __flow_is 2 crewai' -a traces -d 'Trace collection'
complete -c flow -n '__flow_root >/dev/null; and __flow_count 2; and __flow_is 2 crewai' -a log-tasks-outputs -d 'Latest task outputs'
complete -c flow -n '__flow_root >/dev/null; and __flow_count 2; and __flow_is 2 crewai' -a replay -d 'Replay from a task'
complete -c flow -n '__flow_root >/dev/null; and __flow_count 3; and __flow_is 2 crewai; and __flow_is 3 flow' -a plot -d 'Draw the flow'
complete -c flow -n '__flow_root >/dev/null; and __flow_count 3; and __flow_is 2 crewai; and __flow_is 3 traces' -a 'enable disable status'
complete -c flow -n '__flow_root >/dev/null; and __flow_count 3; and __flow_is 2 crewai; and __flow_is 3 deploy' -a validate -d 'Check before deploying'
complete -c flow -n '__flow_root >/dev/null; and __flow_count 3; and __flow_is 2 crewai; and __flow_is 3 deploy' -a status -d 'Deployment status (-u <uuid>)'
complete -c flow -n '__flow_root >/dev/null; and __flow_count 3; and __flow_is 2 crewai; and __flow_is 3 deploy' -a logs -d 'Deployment logs (-u <uuid>)'
complete -c flow -n '__flow_root >/dev/null; and __flow_count 3; and __flow_is 2 crewai; and __flow_is 3 deploy' -a push -d 'Deploy (prefer bin/deploy --amp)'
complete -c flow -n '__flow_root >/dev/null; and __flow_count 3; and __flow_is 2 crewai; and __flow_is 3 deploy' -a list -d 'All deployments'
complete -c flow -n '__flow_root >/dev/null; and __flow_count 4; and __flow_is 2 crewai; and __flow_is 3 deploy' -a -u -d 'By UUID (the project name is not the deployment name)'
complete -c flow -n '__flow_root >/dev/null; and __flow_is 3 deploy; and test (__flow_words)[-1] = -u' -a '(__flow_uuids)'

# python: files, as python itself would
complete -c flow -n '__flow_root >/dev/null; and __flow_count 2; and __flow_is 2 python' -F
