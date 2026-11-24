#!/usr/local/cpanel/3rdparty/bin/perl
# WHM root entry for CCT. Operates on cPanel account homes only.
use strict;
use warnings;
use lib '/usr/local/cpanel';
use Encode                qw(encode);
use IPC::Open2            qw(open2);
use IO::Handle            ();
use MIME::Base64          qw(encode_base64);
use JSON::PP              ();

my $json = JSON::PP->new->ascii(1)->allow_nonref(1);

# Load cPanel helpers only when needed so `perl -c` works outside WHM (CI).
sub cpanel_run {
    require Cpanel::SafeRun::Object;
    return Cpanel::SafeRun::Object->new(@_);
}

sub decode_cpanel_json {
    my ($text) = @_;
    my $document = eval { $json->decode($text) };
    die 'Invalid JSON' unless ref($document) eq 'HASH';
    return $document;
}

my $plugin = '/usr/local/cpanel/base/frontend/jupiter/cct';
my $whm_ui = '/usr/local/cpanel/whostmgr/docroot/cgi/cct';
my $deadline = time() + 120;

sub fail {
    my ($status, $message) = @_;
    my $body = $json->encode({ error => $message });
    my $bytes = encode('UTF-8', $body);
    print "Content-Type: application/json; charset=utf-8\r\nStatus: $status\r\nCache-Control: no-store\r\n",
      "X-Content-Type-Options: nosniff\r\nContent-Length: ", length($bytes), "\r\n\r\n", $bytes;
    exit 0;
}

# Only the real root WHM session may use this addon.
fail('403 Forbidden', 'CCT WHM solo está disponible para root.')
  unless defined $< && $< == 0 && ($ENV{REMOTE_USER} // '') eq 'root';

my $query = $ENV{QUERY_STRING} // '';
my %params = map {
    my ($k, $v) = split /=/, $_, 2;
    $k // '' => do { $v //= ''; $v =~ s/\+/ /g; $v =~ s/%([0-9A-Fa-f]{2})/chr(hex($1))/eg; $v }
} split /&/, $query;

my $action = $params{action} // 'page';
if (($ENV{REQUEST_METHOD} // 'GET') eq 'GET' && $action eq 'page') {
    my $html = do {
        open my $fh, '<:raw', "$whm_ui/index.html" or fail('500 Internal Server Error', 'Falta la interfaz WHM de CCT.');
        local $/; <$fh>;
    };
    my $bytes = $html;
    print "Content-Type: text/html; charset=utf-8\r\nStatus: 200 OK\r\nCache-Control: no-store\r\n",
      "Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'self'; base-uri 'none'; form-action 'self'\r\n",
      "X-Content-Type-Options: nosniff\r\nContent-Length: ", length($bytes), "\r\n\r\n", $bytes;
    exit 0;
}

# Static assets for the WHM UI (same CGI directory).
if (($ENV{REQUEST_METHOD} // 'GET') eq 'GET' && $action eq 'asset') {
    my $name = $params{file} // '';
    fail('404 Not Found', 'Recurso no disponible.')
      unless $name =~ /\A(?:app|model|activity|styles)\.(?:js|css)\z/;
    my $path = -f "$whm_ui/$name" ? "$whm_ui/$name" : "$plugin/$name";
    open my $fh, '<:raw', $path or fail('404 Not Found', 'Recurso no disponible.');
    local $/;
    my $bytes = <$fh>;
    my $type = $name =~ /\.css\z/ ? 'text/css; charset=utf-8' : 'application/javascript; charset=utf-8';
    print "Content-Type: $type\r\nStatus: 200 OK\r\nCache-Control: no-store\r\n",
      "Content-Length: ", length($bytes), "\r\n\r\n", $bytes;
    exit 0;
}

my $target = $params{user} // '';
fail('400 Bad Request', 'Indica una cuenta de cPanel válida.')
  unless $action eq 'accounts' || $target =~ /\A[a-z][a-z0-9_-]{0,31}\z/;

open my $configuration, '<', "$plugin/python-path" or fail('500 Internal Server Error', 'CCT no está instalado en el tema Jupiter.');
chomp(my $python = <$configuration> // '');
close $configuration;
fail('500 Internal Server Error', 'Python de CCT no configurado.')
  unless $python =~ m{\A/[a-zA-Z0-9_./+-]+\z} && -x $python;

my $length = $ENV{CONTENT_LENGTH};
$length = '0' if !defined($length) || $length eq '';
fail('400 Bad Request', 'Solicitud inválida.') unless $length =~ /\A[0-9]+\z/ && $length <= 1048576;
my $body = '';
binmode STDIN;
while (length($body) < $length) {
    my $read = read(STDIN, my $chunk, $length - length($body));
    fail('400 Bad Request', 'Cuerpo incompleto.') unless $read;
    $body .= $chunk;
}

if (($ENV{REQUEST_METHOD} // '') eq 'POST') {
    my $ctype = $ENV{CONTENT_TYPE} // '';
    fail('400 Bad Request', 'Solicitud inválida.')
      unless $ctype =~ m{\Aapplication/json\b} && ($ENV{HTTP_X_CCT_REQUEST} // '') eq '1';
}

local $ENV{CCT_VIA} = 'whm';
local $ENV{CCT_TARGET_USER} = $target if $target ne '';
local $ENV{QUERY_STRING} = $query;

my %allowed = map { $_ => 1 } (
    'uapi/Quota/get_quota_info',
    'uapi/Email/list_pops', 'uapi/Mailboxes/get_mailbox_status_list',
    'uapi/Mailboxes/expunge_messages_for_mailbox_guid',
    'api2/DiskUsage/clearcache', 'api2/DiskUsage/buildcache',
    'api2/DiskUsage/fetchdiskusagewithextras',
    'whm/listaccts'
);

sub whm_json {
    my (@args) = @_;
    my $run = cpanel_run(
        program => '/usr/local/cpanel/bin/whmapi1',
        args    => [ @args, '--output=json' ],
        timeout => 30,
    );
    die 'WHM API failed' if !$run || $run->CHILD_ERROR();
    return decode_cpanel_json($run->stdout());
}

sub uapi_as_user {
    my ($user, $module, $function, $params) = @_;
    my @args = ('--user=' . $user, '--output=json', $module, $function);
    for my $key (sort keys %{$params || {}}) {
        my $value = $params->{$key};
        next unless defined $value && !ref($value);
        push @args, "$key=$value";
    }
    my $run = cpanel_run(
        program => '/usr/local/cpanel/bin/uapi',
        args    => \@args,
        timeout => 12,
    );
    die 'UAPI failed' if !$run || $run->CHILD_ERROR();
    return decode_cpanel_json($run->stdout());
}

sub api2_as_user {
    my ($user, $module, $function, $params) = @_;
    my @args = ('--user=' . $user, '--output=json', $module, $function);
    for my $key (sort keys %{$params || {}}) {
        my $value = $params->{$key};
        next unless defined $value && !ref($value);
        push @args, "$key=$value";
    }
    my $run = cpanel_run(
        program => '/usr/local/cpanel/bin/cpapi2',
        args    => \@args,
        timeout => 12,
    );
    die 'API2 failed' if !$run || $run->CHILD_ERROR();
    return decode_cpanel_json($run->stdout());
}

my ($reader, $writer);
my $child = open2($reader, $writer, $python, '-I', "$plugin/cct_backend.py");
$writer->autoflush(1);
print {$writer} $json->encode({ type => 'start', body_b64 => encode_base64($body, '') }), "\n";

my $response;
my $ok = eval {
    local $SIG{ALRM} = sub { die "CCT WHM timed out\n" };
    alarm 120;
    while (my $line = <$reader>) {
        die 'Oversized backend frame' if length($line) > 16 * 1024 * 1024;
        my $frame = $json->decode($line);
        die 'Invalid backend frame' unless ref($frame) eq 'HASH';
        if (($frame->{type} // '') eq 'response') {
            $response = $frame;
            last;
        }
        die 'Unexpected backend operation' unless ($frame->{type} // '') eq 'api';
        my ($version, $module, $function) = @{$frame}{qw(version module function)};
        die 'Invalid API method' if grep { !defined($_) || ref($_) } ($version, $module, $function);
        my $key = join '/', $version, $module, $function;
        die 'API method not permitted' unless $allowed{$key} && ref($frame->{params}) eq 'HASH';
        my $remaining = int($deadline - time());
        die 'Request timed out' if $remaining < 1;
        alarm($remaining < 12 ? $remaining : 12);
        my $document;
        if ($version eq 'whm' && $module eq 'listaccts') {
            $document = whm_json('listaccts');
        }
        elsif ($version eq 'uapi') {
            die 'Missing target user' if $target eq '';
            $document = uapi_as_user($target, $module, $function, $frame->{params});
        }
        elsif ($version eq 'api2') {
            die 'Missing target user' if $target eq '';
            $document = api2_as_user($target, $module, $function, $frame->{params});
        }
        else {
            die 'Unsupported API family';
        }
        alarm(int($deadline - time()) > 0 ? int($deadline - time()) : 1);
        print {$writer} $json->encode({ type => 'api_result', ok => JSON::PP::true, document => $document }), "\n";
    }
    close $writer;
    close $reader;
    waitpid($child, 0);
    $child = undef;
    die 'Backend did not finish cleanly' if $? != 0 || !$response;
    alarm 0;
    1;
};
alarm 0;
if (!$ok) {
    my $failure = "$@";
    $failure =~ s/[\r\n]+/ /g;
    warn 'CCT WHM: ', substr($failure, 0, 1200), "\n";
    close $writer if $writer;
    close $reader if $reader;
    if ($child) { kill 'TERM', $child; waitpid($child, 0); }
    fail('500 Internal Server Error', 'No se pudo completar la operación WHM de CCT.');
}

my $status = $response->{status} // '200 OK';
my $type = $response->{content_type} // 'application/json; charset=utf-8';
my $bytes = encode('UTF-8', $response->{body} // '');
print "Status: $status\r\nContent-Type: $type\r\nCache-Control: no-store\r\n",
  "X-Content-Type-Options: nosniff\r\nContent-Length: ", length($bytes), "\r\n\r\n", $bytes;
1;
