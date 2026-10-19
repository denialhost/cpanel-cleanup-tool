#!/usr/local/cpanel/3rdparty/bin/perl
use strict;
use warnings;
use lib '/usr/local/cpanel';
use FindBin ();
use Encode qw(encode);
use JSON::PP ();
use MIME::Base64 qw(encode_base64);
use IPC::Open2 qw(open2);
use IO::Handle ();
use Time::HiRes qw(time);

my $json = JSON::PP->new->ascii(1);
my ($live, $child, $reader, $writer, $response);
my $deadline = time() + 120;
my %allowed = map { $_ => 1 } (
    'uapi/Features/has_feature', 'uapi/Quota/get_quota_info',
    'uapi/Email/list_pops', 'uapi/Mailboxes/get_mailbox_status_list',
    'uapi/Mailboxes/expunge_messages_for_mailbox_guid', 'api2/DiskUsage/clearcache'
);

# LiveAPI may emit diagnostics. Keep them out of the one HTTP response.
sub vendor_call {
    my ($callback) = @_;
    my $diagnostics = '';
    open my $sink, '>', \$diagnostics or die 'Cannot capture LiveAPI output';
    local *STDOUT = $sink;
    return $callback->();
}

my $ok = eval {
    die 'Root is not an authenticated cPanel account' if $> == 0;
    local $SIG{ALRM} = sub { die "CCT LiveAPI request timed out\n" };
    alarm 120;
    require Cpanel::LiveAPI;
    $live = vendor_call(sub { Cpanel::LiveAPI->new() });
    die 'LiveAPI did not connect' unless $live;

    # cpsrvd supplies an empty CONTENT_LENGTH for requests without a body.
    # CGI treats an absent or empty value as no body, including the page GET.
    my $length = $ENV{CONTENT_LENGTH};
    $length = '0' if !defined($length) || $length eq '';
    die 'Invalid request length' unless $length =~ /\A[0-9]+\z/ && $length <= 1048576;
    my $body = '';
    binmode STDIN;
    while (length($body) < $length) {
        my $read = read(STDIN, my $chunk, $length - length($body));
        die 'Incomplete request body' unless $read;
        $body .= $chunk;
    }
    open my $configuration, '<', "$FindBin::RealBin/python-path" or die 'Missing Python configuration';
    chomp(my $python = <$configuration> // '');
    close $configuration;
    die 'Invalid Python configuration' unless $python =~ m{\A/[a-zA-Z0-9_./+-]+\z} && -x $python;
    $child = open2($reader, $writer, $python, '-I', "$FindBin::RealBin/cct_backend.py");
    $writer->autoflush(1);
    print {$writer} $json->encode({type => 'start', body_b64 => encode_base64($body, '')}), "\n";
    my $feature_checked = 0;
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
        die 'Feature check required' unless $feature_checked || $key eq 'uapi/Features/has_feature';
        die 'Wrong feature' if $key eq 'uapi/Features/has_feature' && ($frame->{params}{name} // '') ne 'cct';
        my $remaining = int($deadline - time());
        die 'Request timed out' if $remaining < 1;
        alarm($remaining < 12 ? $remaining : 12);
        my $document = vendor_call(sub { $live->$version($module, $function, $frame->{params}) });
        alarm(int($deadline - time()) > 0 ? int($deadline - time()) : 1);
        if ($key eq 'uapi/Features/has_feature' && ref($document) eq 'HASH') {
            my $result = $document->{cpanelresult} // $document;
            $result = $result->{result} // $result if ref($result) eq 'HASH';
            $feature_checked = ref($result) eq 'HASH' && ($result->{status} // 0) == 1;
        }
        print {$writer} $json->encode({type => 'api_result', ok => JSON::PP::true, document => $document}), "\n";
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
    warn 'CCT: LiveAPI/backend request failed: ', substr($failure, 0, 1200), "\n";
    close $writer if $writer;
    close $reader if $reader;
    if ($child) { kill 'TERM', $child; waitpid($child, 0); }
    $response = {status => '500 Internal Server Error', content_type => 'application/json; charset=utf-8',
        body => $json->encode({error => 'No se pudo conectar el plugin con LiveAPI de cPanel. Contacta al administrador.'})};
}
if ($live) { eval { vendor_call(sub { $live->end() }); }; }
my $status = $response->{status} // '';
my $type = $response->{content_type} // '';
if ($status !~ /\A(?:200 OK|400 Bad Request|404 Not Found|500 Internal Server Error)\z/
    || $type !~ /\A(?:application\/json|text\/html); charset=utf-8\z/ || ref($response->{body})) {
    $status = '500 Internal Server Error';
    $type = 'application/json; charset=utf-8';
    $response->{body} = '{"error":"Respuesta interna no valida."}';
}
my $bytes = encode('UTF-8', $response->{body} // '');
binmode STDOUT;
print "Status: $status\r\nContent-Type: $type\r\nCache-Control: no-store\r\n",
    "X-Content-Type-Options: nosniff\r\nReferrer-Policy: no-referrer\r\n",
    "Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'self'; base-uri 'none'; form-action 'self'\r\n",
    'Content-Length: ', length($bytes), "\r\n\r\n", $bytes;
1;
