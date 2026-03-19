#!/usr/bin/env ruby
# spdif-supervisor.rb
#
# Switches between Snapcast and JACK S/PDIF passthrough based on signal detection.
#
# Modes:
#   :snapcast - snapclient runs as a systemd service; ALSA is polled for S/PDIF signal
#   :jack     - jackd runs for S/PDIF passthrough; watches for carrier loss and
#               Snapcast server activity
#
# Always starts in :snapcast mode.

require "json"
require "net/http"
require "open3"
require "logger"

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CARD_NAME             = "ICUSBAUDIO7D"
CAPTURE_DEVICE        = "hw:#{CARD_NAME},0"
PLAYBACK_DEVICE       = "hw:sndrpimerusamp,0"

CARD = Dir["/proc/asound/card*"].find { |d|
  File.read("#{d}/id").strip == CARD_NAME rescue false
}&.then { |d| d[/\d+$/] } or abort("Card #{CARD_NAME} not found in /proc/asound")

CARRIER_STATUS_FILE   = "/proc/asound/card#{CARD}/pcm0c/sub0/status"
LOG_FILE              = "/var/log/spdif-supervisor.log"

SNAPCAST_SERVICE      = "snapclient"
SNAPCAST_SERVER       = "evolution"
SNAPCAST_PORT         = 1780
SNAPCAST_STREAM       = "default"
SNAPCAST_POLL_INTERVAL = 5      # seconds between Snapcast API polls in JACK mode

RT_PRIORITY           = 80
CPU_CORES             = "2,3"
RATE                  = 48_000
PERIOD_SIZE           = 1024
NUM_PERIODS           = 2

SILENCE_THRESHOLD      = 0.0008 # RMS below this = no S/PDIF signal
SILENCE_PROBE_DURATION = 1      # seconds per arecord probe
PROBE_FILE             = "/tmp/spdif_probe.wav"

SPDIF_DEBOUNCE        = 3       # consecutive active reads before switching to JACK
SNAPCAST_DEBOUNCE     = 3       # consecutive 'playing' reads before switching to Snapcast

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

$logger = Logger.new(
  File.open(LOG_FILE, "a").tap { |f| f.sync = true },
  progname: "spdif-supervisor",
  formatter: proc { |sev, time, prog, msg|
    "#{time.strftime('%Y-%m-%d %H:%M:%S')} [#{prog}] #{sev}: #{msg}\n"
  }
)

# Also mirror to stdout so journald/systemd picks it up
$stdout.sync = true
def log(level, msg)
  $logger.send(level, msg)
  puts "#{Time.now.strftime('%Y-%m-%d %H:%M:%S')} [#{level.upcase}] #{msg}"
end

# ---------------------------------------------------------------------------
# Snapcast control
# ---------------------------------------------------------------------------

def start_snapcast
  log :info, "Starting snapclient"
  system("systemctl", "start", SNAPCAST_SERVICE) or
  log(:warn, "Failed to start #{SNAPCAST_SERVICE}")
end

def stop_snapcast
  log :info, "Stopping snapclient"
  system("systemctl", "stop", SNAPCAST_SERVICE) or
  log(:warn, "Failed to stop #{SNAPCAST_SERVICE}")
end

# ---------------------------------------------------------------------------
# JACK control
# ---------------------------------------------------------------------------

$jack_pid = nil

def start_jack
  log :info, "Starting JACK passthrough"
  system("cpufreq-set", "-g", "performance")

  env = { "JACK_NO_AUDIO_RESERVATION" => "1" }
  cmd = [
    "taskset", "-c", CPU_CORES,
    "chrt", "-f", RT_PRIORITY.to_s,
    "jackd", "-R", "-P", RT_PRIORITY.to_s,
    "-d", "alsa",
    "-C", CAPTURE_DEVICE,
    "-P", PLAYBACK_DEVICE,
    "-r", RATE.to_s,
    "-p", PERIOD_SIZE.to_s,
    "-n", NUM_PERIODS.to_s
  ]
  # log :debug, '$ '+cmd.join(' ')

  log_file = File.open(LOG_FILE, "a")
  $jack_pid = spawn(env, *cmd, out: log_file, err: log_file)
  log :info, "jackd started (PID #{$jack_pid})"

  # Wait for JACK socket to appear
  socket_ready = 20.times.any? do
    sleep 0.5
    Dir.glob("/dev/shm/jack_*").any? || Dir.glob("/tmp/jack-*/default/jack_0").any?
  end

  unless socket_ready
    log :error, "JACK socket never appeared"
    stop_jack
    return false
  end

  log :info, "Connecting JACK ports"
  cmd = ["jack_connect", "system:capture_1", "system:playback_1"]
  # log :debug, '$ '+cmd.join(' ')
  system(*cmd)
  cmd = ["jack_connect", "system:capture_2", "system:playback_2"]
  # log :debug, '$ '+cmd.join(' ')
  system(*cmd)
  log :info, "JACK pipeline active"
  true
end

def stop_jack
  if $jack_pid
    begin
      Process.kill("TERM", $jack_pid)
      Process.wait($jack_pid)
      log :info, "jackd stopped (PID #{$jack_pid})"
    rescue Errno::ESRCH, Errno::ECHILD
      # already gone
    end
    $jack_pid = nil
  end
  system("cpufreq-set", "-g", "ondemand")
end

def jack_alive?
  return false unless $jack_pid
  Process.kill(0, $jack_pid)
  true
rescue Errno::ESRCH
  false
end

# ---------------------------------------------------------------------------
# Signal detection
# ---------------------------------------------------------------------------

# Records a short burst from the S/PDIF input and checks RMS amplitude.
# Returns true if a signal above the threshold is present.
def spdif_signal_present?
  system(
    "arecord",
    "-D", CAPTURE_DEVICE,
    "-f", "S16_LE",
    "-r", RATE.to_s,
    "-c", "2",
    "-d", SILENCE_PROBE_DURATION.to_s,
    "-q", PROBE_FILE,
    out: "/dev/null", err: "/dev/null"
  ) or return false

  stat, = Open3.capture2e("sox", PROBE_FILE, "-n", "stat")
  rms_line = stat.lines.grep(/RMS\s+amplitude/).first or return false
  rms = rms_line.split.last.to_f
  rms > SILENCE_THRESHOLD
end

# Reads trigger_time from the ALSA PCM status file.
# Returns the value as a string, or nil if unreadable.
def read_trigger_time
  t = File.readlines(CARRIER_STATUS_FILE)
  return true if t.compact.first.strip == 'closed'
  t = t.grep(/trigger_time:/).first&.split&.last
  t ? t.to_i : 0
rescue Errno::ENOENT
  0
end

# Checks that trigger_time is non-nil and stable across several reads.
# A real locked S/PDIF clock holds a fixed value; noise/unpowered devices don't.
def spdif_carrier_stable?(checks: SPDIF_DEBOUNCE, interval: 0.3)
  times = (1..checks).map do |i|
    sleep interval unless i == 1
    read_trigger_time
  end
  first = times.first
  return false if first.nil?
  times.all? { |t| t == first }
end

# Queries the Snapcast JSON-RPC API and returns true if the default stream is playing.
def snapcast_playing?
  uri = URI("http://#{SNAPCAST_SERVER}:#{SNAPCAST_PORT}/jsonrpc")
  request = Net::HTTP::Post.new(uri, "Content-Type" => "application/json")
  request.body = JSON.generate(id: 1, jsonrpc: "2.0", method: "Server.GetStatus")

  response = Net::HTTP.start(uri.host, uri.port, open_timeout: 3, read_timeout: 3) do |http|
    http.request(request)
  end

  data = JSON.parse(response.body)
  streams = data.dig("result", "server", "streams") || []
  stream = streams.find { |s| s["id"] == SNAPCAST_STREAM }
  stream&.fetch("status") == "playing"
rescue => e
  log :warn, "Snapcast API error: #{e.message}"
  false
end

# ---------------------------------------------------------------------------
# Mode: SNAPCAST
# Polls for S/PDIF signal. Switches to JACK after SPDIF_DEBOUNCE hits.
# ---------------------------------------------------------------------------

def run_snapcast_mode
  log :info, "Entering SNAPCAST mode"
  start_snapcast
  active_count = 0

  loop do
    if spdif_signal_present?
      active_count += 1
      log :info, "S/PDIF signal detected (#{active_count}/#{SPDIF_DEBOUNCE})"
      if active_count >= SPDIF_DEBOUNCE
        if spdif_carrier_stable?
          log :info, "S/PDIF stable — switching to JACK"
          stop_snapcast
          return
        else
          log :info, "S/PDIF signal present but carrier unstable — resetting debounce"
          active_count = 0
        end
      end
    else
      log(:info, "S/PDIF signal lost — resetting debounce") if active_count > 0
      active_count = 0
    end
    # No sleep needed: arecord probe takes SILENCE_PROBE_DURATION seconds itself
  end
end

# ---------------------------------------------------------------------------
# Mode: JACK
# Watches for carrier loss (trigger_time change) and Snapcast activity.
# Switches back to Snapcast on either condition.
# ---------------------------------------------------------------------------

def run_jack_mode
  log :info, "Entering JACK mode"
  unless start_jack
    log :warn, "JACK failed to start — falling back to Snapcast"
    return
  end

  last_trigger    = read_trigger_time
  snapcast_count  = 0
  last_snap_check = Time.now - SNAPCAST_POLL_INTERVAL  # check immediately on entry

  loop do
    # --- Carrier loss (fast path, checked every iteration) ---
    current_trigger = read_trigger_time
    delta = (last_trigger - current_trigger).magnitude
    # log :debug, current_trigger
    if delta > 650
      log :info, "Carrier loss detected (trigger_time changed by #{delta}) — switching to Snapcast"
      stop_jack
      return
    end

    # --- JACK process health ---
    unless jack_alive?
      log :warn, "jackd died unexpectedly — switching to Snapcast"
      $jack_pid = nil
      return
    end

    # --- Snapcast activity (checked every SNAPCAST_POLL_INTERVAL seconds) ---
    if Time.now - last_snap_check >= SNAPCAST_POLL_INTERVAL
      last_snap_check = Time.now
      if snapcast_playing?
        snapcast_count += 1
        log :info, "Snapcast stream active (#{snapcast_count}/#{SNAPCAST_DEBOUNCE})"
        if snapcast_count >= SNAPCAST_DEBOUNCE
          log :info, "Snapcast stable — switching back"
          stop_jack
          return
        end
      else
        log(:info, "Snapcast went idle — resetting debounce") if snapcast_count > 0
        snapcast_count = 0
      end
    end

    sleep 0.2
  end
end

# ---------------------------------------------------------------------------
# Cleanup on exit
# ---------------------------------------------------------------------------

at_exit do
  log :info, "Supervisor exiting — cleaning up"
  stop_jack
  start_snapcast
end

%w[INT TERM].each do |sig|
  trap(sig) { exit }
end

# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

log :info, "Supervisor started (PID #{Process.pid})"
state = :snapcast

loop do
  case state
  when :snapcast
    run_snapcast_mode
    state = :jack
  when :jack
    run_jack_mode
    state = :snapcast
  end
end
