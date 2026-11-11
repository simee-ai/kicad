/*
 * This program source code file is part of KiCad, a free EDA CAD application.
 *
 * Copyright The KiCad Developers, see AUTHORS.txt for contributors.
 *
 * This program is free software: you can redistribute it and/or modify it
 * under the terms of the GNU General Public License as published by the
 * Free Software Foundation, either version 3 of the License, or (at your
 * option) any later version.
 *
 * This program is distributed in the hope that it will be useful, but
 * WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
 * General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License along
 * with this program.  If not, see <http://www.gnu.org/licenses/>.
 */

#include "pcb_tune_job.h"

#include <board.h>
#include <board_commit.h>
#include <board_design_settings.h>
#include <cli/exit_codes.h>
#include <drc/drc_engine.h>
#include <footprint.h>
#include <jobs/job_pcb_tune.h>
#include <length_delay_calculation/length_delay_calculation.h>
#include <netinfo.h>
#include <pad.h>
#include <pcb_io/pcb_io_mgr.h>
#include <reporter.h>
#include <tool/tool_manager.h>
#include <tools/zone_filler_tool.h>
#include <pcb_generator.h>
#include <pcb_track.h>
#include <tool/edit_points.h>
#include <tools/generator_tool.h>
#include <generators/pcb_tuning_pattern.h> // after the four above: it needs them
#include <nlohmann/json.hpp>
#include <wx/ffile.h>


namespace
{
using MODE = JOB_PCB_TUNE::MODE;

/// How far a target given as one number may be missed: KiCad's own length tolerance
const int64_t TOLERANCE = PNS::MEANDER_SETTINGS::DEFAULT_LENGTH_TOLERANCE;

/// Retunes of a pattern after its first tuning while DRC still measures it outside its target
const int MAX_RETUNES = 3;


/// A target length or skew (IU): aim at opt, accept anything from min to max
struct TARGET
{
    std::optional<int64_t> min;
    int64_t                opt = 0;
    std::optional<int64_t> max;

    bool Below( int64_t aValue ) const { return min && aValue < *min; }
    bool Above( int64_t aValue ) const { return max && aValue > *max; }
    bool Contains( int64_t aValue ) const { return !Below( aValue ) && !Above( aValue ); }

    /// The tuner's range: this one moved by aOffset, so that it aims at opt + aOffset
    MINOPTMAX<int> Shifted( int64_t aOffset ) const
    {
        MINOPTMAX<int> range;
        range.SetOpt( static_cast<int>( opt + aOffset ) );

        if( min )
            range.SetMin( static_cast<int>( *min + aOffset ) );

        if( max )
            range.SetMax( static_cast<int>( *max + aOffset ) );

        return range;
    }
};


TARGET around( int64_t aOpt )
{
    return { aOpt - TOLERANCE, aOpt, aOpt + TOLERANCE };
}


/**
 * A DRC rule's range as a target. A length aims at the rule's opt, else the middle of its min and
 * max: KiCad's tuner would aim at the min (MINOPTMAX's opt defaults to it) and stop just short.
 * A skew is held to the rule's max either way round, as DRC holds it, and aims at its opt or 0.
 */
std::optional<TARGET> ruleTarget( const DRC_CONSTRAINT& aConstraint, bool aSkew, wxString& aError )
{
    if( aConstraint.IsNull() )
        return std::nullopt;

    if( aConstraint.GetOption( DRC_CONSTRAINT::OPTIONS::TIME_DOMAIN ) )
    {
        aError = wxString::Format( wxS( "rule '%s' is in the time domain, which tune does not "
                                        "support" ),
                                   aConstraint.GetName() );
        return std::nullopt;
    }

    const MINOPTMAX<int>& value = aConstraint.GetValue();
    TARGET                target;

    if( aSkew )
    {
        if( value.HasMax() )
        {
            target.min = -static_cast<int64_t>( value.Max() );
            target.max = value.Max();
        }

        target.opt = value.HasOpt() ? value.Opt() : 0;
        return target;
    }

    if( value.HasMin() )
        target.min = value.Min();

    if( value.HasMax() )
        target.max = value.Max();

    if( value.HasOpt() )
        target.opt = value.Opt();
    else if( value.HasMin() && value.HasMax() )
        target.opt = ( static_cast<int64_t>( value.Min() ) + value.Max() ) / 2;
    else if( value.HasMin() )
        target.opt = value.Min() + TOLERANCE;
    else
        target.opt = value.Max() - TOLERANCE;

    return target;
}


/// A net's length as DRC's length rules measure it: its tracks, vias and pads, on every layer
int64_t lengthAsDrc( BOARD* aBoard, const NETINFO_ITEM* aNet )
{
    LENGTH_DELAY_CALCULATION*                  calc = aBoard->GetLengthCalculation();
    std::vector<LENGTH_DELAY_CALCULATION_ITEM> items;

    auto add =
            [&]( const BOARD_CONNECTED_ITEM* aItem )
            {
                if( aItem->GetNetCode() != aNet->GetNetCode() )
                    return;

                LENGTH_DELAY_CALCULATION_ITEM item = calc->GetLengthCalculationItem( aItem );

                if( item.Type() != LENGTH_DELAY_CALCULATION_ITEM::TYPE::UNKNOWN )
                    items.emplace_back( item );
            };

    for( PCB_TRACK* track : aBoard->Tracks() )
        add( track );

    for( FOOTPRINT* footprint : aBoard->Footprints() )
    {
        for( PAD* pad : footprint->Pads() )
            add( pad );
    }

    // As DRC_TEST_PROVIDER_MATCHED_LENGTH calculates it
    constexpr PATH_OPTIMISATIONS opts = {
        .OptimiseVias = true, .MergeTracks = true, .OptimiseTracesInPads = true, .InferViaInPad = false
    };

    LENGTH_DELAY_STATS details = calc->CalculateLengthDetails( items, opts, nullptr, nullptr,
                                                               LENGTH_DELAY_LAYER_OPT::NO_LAYER_DETAIL,
                                                               LENGTH_DELAY_DOMAIN_OPT::NO_DELAY_DETAIL );

    return details.TrackLength + details.ViaLength + details.PadToDieLength;
}


/// The net's longest straight track outside any tuning pattern
PCB_TRACK* longestTrack( BOARD* aBoard, const NETINFO_ITEM* aNet )
{
    PCB_TRACK* longest = nullptr;

    for( PCB_TRACK* track : aBoard->Tracks() )
    {
        if( track->Type() != PCB_TRACE_T || track->GetNetCode() != aNet->GetNetCode() )
            continue;

        if( dynamic_cast<PCB_TUNING_PATTERN*>( track->GetParentGroup() ) )
            continue;

        if( !longest || track->GetLength() > longest->GetLength() )
            longest = track;
    }

    return longest;
}


const char* modeName( MODE aMode )
{
    switch( aMode )
    {
    case MODE::DIFF_PAIR: return "diff-pair";
    case MODE::SKEW:      return "skew";
    default:              return "single";
    }
}


/// One net's outcome, as the report gives it
struct NET_RESULT
{
    wxString               net;
    wxString               coupled;      ///< the pair's other net (diff-pair and skew)
    MODE                   mode = MODE::SINGLE;
    std::optional<TARGET>  target;       ///< a length, or for skew a skew
    int64_t                before = 0;   ///< DRC's length of the net
    int64_t                after = 0;
    int64_t                coupledBefore = 0;
    int64_t                coupledAfter = 0;
    wxString               action = wxS( "none" ); ///< placed, retuned, skipped (within target), none
    int                    retunes = 0;  ///< after the first tuning, by DRC's measure
    wxString               tunerStatus;  ///< the tuning pattern's own last status
    wxString               status;       ///< by DRC's measure: tuned, too_short, too_long, failed
    wxString               error;

    /// What the target holds: the net's length, the pair's longer one, or the skew
    int64_t Measure( int64_t aLength, int64_t aCoupled ) const
    {
        switch( mode )
        {
        case MODE::DIFF_PAIR: return std::max( aLength, aCoupled );
        case MODE::SKEW:      return aLength - aCoupled;
        default:              return aLength;
        }
    }

    int64_t MeasureBefore() const { return Measure( before, coupledBefore ); }
    int64_t MeasureAfter() const { return Measure( after, coupledAfter ); }

    /// Judge the net by DRC's measure. A pair tuned as one is tuned only if both its nets are.
    void Judge()
    {
        int64_t measure = MeasureAfter();

        if( target->Below( measure ) || ( mode == MODE::DIFF_PAIR && target->Below( std::min( after, coupledAfter ) ) ) )
            status = wxS( "too_short" );
        else if( target->Above( measure ) )
            status = wxS( "too_long" );
        else
            status = wxS( "tuned" );
    }
};


class PCB_TUNER
{
public:
    PCB_TUNER( JOB_PCB_TUNE& aJob, BOARD* aBoard, GENERATOR_TOOL* aTool ) :
            m_job( aJob ),
            m_board( aBoard ),
            m_tool( aTool )
    {
        m_mode = aJob.m_mode == MODE::DIFF_PAIR ? LENGTH_TUNING_MODE::DIFF_PAIR
                 : aJob.m_mode == MODE::SKEW    ? LENGTH_TUNING_MODE::DIFF_PAIR_SKEW
                                                : LENGTH_TUNING_MODE::SINGLE;
    }

    /// Tune aNet; aGroupLength is the longest net of a skew group (no --length)
    NET_RESULT Tune( NETINFO_ITEM* aNet, std::optional<int64_t> aGroupLength )
    {
        NET_RESULT result;
        result.net = aNet->GetNetname();
        result.mode = m_job.m_mode;

        NETINFO_ITEM* coupled = nullptr;

        if( m_job.m_mode != MODE::SINGLE )
        {
            coupled = m_board->DpCoupledNet( aNet );

            if( !coupled )
                return fail( result, wxS( "is not in a differential pair (its name needs a _P/_N or "
                                          "+/- suffix)" ) );

            result.coupled = coupled->GetNetname();
        }

        auto measure =
                [&]( int64_t& aLength, int64_t& aCoupled )
                {
                    aLength = lengthAsDrc( m_board, aNet );
                    aCoupled = coupled ? lengthAsDrc( m_board, coupled ) : 0;
                };

        measure( result.before, result.coupledBefore );
        result.after = result.before;
        result.coupledAfter = result.coupledBefore;

        PCB_TRACK* longest = longestTrack( m_board, aNet );
        wxString   error;

        result.target = target( aNet, longest, aGroupLength, error );

        if( !result.target )
            return fail( result, error.IsEmpty() ? wxString( wxS( "has no target: give --length or "
                                                                  "--skew, or a length or skew "
                                                                  "rule" ) )
                                                 : error );

        PCB_TUNING_PATTERN* pattern = existingPattern( aNet, coupled );

        if( result.target->Contains( result.MeasureBefore() ) )
        {
            result.action = wxS( "skipped" );
            result.Judge();
            return result;
        }

        if( !pattern )
        {
            // Meanders only lengthen (and a skew pattern only lengthens its own net)
            if( result.target->Above( result.MeasureBefore() ) )
            {
                result.Judge();
                return result;
            }

            if( !longest )
                return fail( result, wxS( "has no straight track to tune" ) );

            pattern = newPattern( longest, aNet, coupled, error );

            if( !pattern )
                return fail( result, error );

            result.action = wxS( "placed" );
        }
        else
        {
            result.action = wxS( "retuned" );
        }

        // Tune, then measure as DRC does. The tuner measures the path between the two pads it
        // finds at the track's ends, and DRC every pad of the net: where pads overlap (a module's
        // castellated pad over its through-hole one), the tuner clips the track at the smaller
        // pad and infers a via to a pad without the track's layer, so its length is off DRC's.
        // Retune by the difference while DRC finds the net outside its target.
        int64_t offset = 0;

        for( int round = 0; ; ++round )
        {
            std::string status = tune( pattern, result.target->Shifted( offset ) );
            result.tunerStatus = wxString::FromUTF8( status );

            measure( result.after, result.coupledAfter );

            int64_t measured = result.MeasureAfter();

            if( result.target->Contains( measured ) || round == MAX_RETUNES || status != "tuned" )
                break;

            offset += result.target->opt - measured;
            result.retunes++;
        }

        result.Judge();
        return result;
    }

private:
    NET_RESULT fail( NET_RESULT& aResult, const wxString& aError )
    {
        aResult.status = wxS( "failed" );
        aResult.error = aError;
        return aResult;
    }

    /// The target: --length or --skew, else the group's longest net, else the board's rule
    std::optional<TARGET> target( NETINFO_ITEM* aNet, PCB_TRACK* aTrack,
                                  std::optional<int64_t> aGroupLength, wxString& aError )
    {
        bool skew = m_job.m_mode == MODE::SKEW;

        if( skew && m_job.m_skew )
            return around( pcbIUScale.mmToIU( *m_job.m_skew ) );

        if( !skew && m_job.m_length )
            return around( pcbIUScale.mmToIU( *m_job.m_length ) );

        if( !skew && aGroupLength )
            return around( *aGroupLength );

        if( !aTrack )
            return std::nullopt;

        std::shared_ptr<DRC_ENGINE>& drc = m_board->GetDesignSettings().m_DRCEngine;

        if( !drc )
            return std::nullopt;

        return ruleTarget( drc->EvalRules( skew ? SKEW_CONSTRAINT : LENGTH_CONSTRAINT, aTrack,
                                           nullptr, aTrack->GetLayer() ),
                           skew, aError );
    }

    /// A pattern of this mode tuned before on the net (or for a pair, on either of its nets)
    PCB_TUNING_PATTERN* existingPattern( NETINFO_ITEM* aNet, NETINFO_ITEM* aCoupled )
    {
        for( PCB_GENERATOR* generator : m_board->Generators() )
        {
            PCB_TUNING_PATTERN* pattern = dynamic_cast<PCB_TUNING_PATTERN*>( generator );

            if( !pattern || pattern->GetTuningMode() != m_mode )
                continue;

            int net = pattern->GetNetCode();

            if( net == aNet->GetNetCode()
                || ( m_mode == LENGTH_TUNING_MODE::DIFF_PAIR && aCoupled && net == aCoupled->GetNetCode() ) )
            {
                return pattern;
            }
        }

        return nullptr;
    }

    /// A pattern on aTrack, as if clicked at both ends of its span, inset 1 mm from them
    PCB_TUNING_PATTERN* newPattern( PCB_TRACK* aTrack, NETINFO_ITEM* aNet, NETINFO_ITEM* aCoupled,
                                    wxString& aError )
    {
        SEG span( aTrack->GetStart(), aTrack->GetEnd() );

        // A pair is tuned only where its two nets run side by side: the span of the segment
        // beside the coupled net's longest segment
        if( m_mode == LENGTH_TUNING_MODE::DIFF_PAIR )
        {
            PCB_TRACK* coupled = longestTrack( m_board, aCoupled );

            if( !coupled )
            {
                aError = wxS( "has no routed pair to tune with" );
                return nullptr;
            }

            VECTOR2I a = span.NearestPoint( coupled->GetStart() );
            VECTOR2I b = span.NearestPoint( coupled->GetEnd() );

            if( ( a - span.A ).EuclideanNorm() > ( b - span.A ).EuclideanNorm() )
                std::swap( a, b );

            span = SEG( a, b );
        }

        // A skew pattern takes a third of the span, so the pair can be tuned as one beside it
        if( m_mode == LENGTH_TUNING_MODE::DIFF_PAIR_SKEW )
            span.B = span.A + ( span.B - span.A ) / 3;

        // Keep the meanders off the pads or bends at the span's ends, as clicks inside it would
        VECTOR2I dir = span.B - span.A;
        int      inset = std::min<int>( pcbIUScale.mmToIU( 1.0 ), dir.EuclideanNorm() / 4 );
        VECTOR2I step = dir.Resize( inset );

        PCB_TUNING_PATTERN* pattern = PCB_TUNING_PATTERN::CreateNew( m_tool, nullptr, aTrack, m_mode );

        pattern->SetPosition( span.A + step );
        pattern->SetEnd( span.B - step );
        return pattern;
    }

    /// Tune aPattern to aRange (a length, or for skew a skew); returns the tuner's status
    std::string tune( PCB_TUNING_PATTERN* aPattern, const MINOPTMAX<int>& aRange )
    {
        PNS::MEANDER_SETTINGS& settings = aPattern->GetSettings();

        if( m_mode == LENGTH_TUNING_MODE::DIFF_PAIR_SKEW )
            settings.SetTargetSkew( aRange );
        else
            settings.SetTargetLength( aRange );

        // Our range, in length (CreateNew marks a skew rule's pattern as time domain)
        settings.m_isTimeDomain = false;
        aPattern->SetOverrideCustomRules( true );

        BOARD_COMMIT commit( m_tool );

        aPattern->EditStart( m_tool, m_board, &commit );
        aPattern->Update( m_tool, m_board, &commit );
        aPattern->EditFinish( m_tool, m_board, &commit );

        commit.Push( wxS( "Tune" ), SKIP_UNDO | SKIP_SET_DIRTY );

        // On the board now: a retune modifies it instead of adding it again
        aPattern->ClearFlags( IS_NEW );

        std::string status;
        aPattern->GetProperties().get_to( "last_status", status );
        return status;
    }

    JOB_PCB_TUNE&      m_job;
    BOARD*             m_board;
    GENERATOR_TOOL*    m_tool;
    LENGTH_TUNING_MODE m_mode;
};


double mm( int64_t aValue )
{
    return pcbIUScale.IUTomm( aValue );
}


nlohmann::json jsonReport( const std::vector<NET_RESULT>& aResults )
{
    nlohmann::json nets = nlohmann::json::array();

    for( const NET_RESULT& r : aResults )
    {
        nlohmann::json net = {
            { "net", r.net.ToStdString( wxConvUTF8 ) },
            { "mode", modeName( r.mode ) },
            { "length_before", mm( r.before ) },
            { "length_after", mm( r.after ) },
            { "action", r.action.ToStdString() },
            { "retunes", r.retunes },
            { "status", r.status.ToStdString() },
        };

        if( r.target )
        {
            auto bound = []( const std::optional<int64_t>& v )
                         {
                             return v ? nlohmann::json( mm( *v ) ) : nlohmann::json();
                         };

            net["target"] = { { "min", bound( r.target->min ) }, { "opt", mm( r.target->opt ) },
                              { "max", bound( r.target->max ) } };
        }

        if( !r.coupled.IsEmpty() )
        {
            net["coupled_net"] = r.coupled.ToStdString( wxConvUTF8 );
            net["coupled_length_before"] = mm( r.coupledBefore );
            net["coupled_length_after"] = mm( r.coupledAfter );
        }

        if( r.mode == MODE::SKEW )
        {
            net["skew_before"] = mm( r.MeasureBefore() );
            net["skew_after"] = mm( r.MeasureAfter() );
        }

        if( !r.tunerStatus.IsEmpty() )
            net["tuner_status"] = r.tunerStatus.ToStdString();

        if( !r.error.IsEmpty() )
            net["error"] = r.error.ToStdString( wxConvUTF8 );

        nets.push_back( net );
    }

    return { { "nets", nets } };
}


wxString textReport( const std::vector<NET_RESULT>& aResults )
{
    wxString text;

    for( const NET_RESULT& r : aResults )
    {
        if( !r.error.IsEmpty() )
        {
            text += wxString::Format( wxS( "%s: %s\n" ), r.net, r.error );
            continue;
        }

        wxString what = r.mode == MODE::SKEW ? wxS( "skew" ) : wxS( "length" );

        text += wxString::Format( wxS( "%s: %s %.4f mm -> %.4f mm, target %.4f mm (%s, %s" ), r.net, what,
                                  mm( r.MeasureBefore() ), mm( r.MeasureAfter() ), mm( r.target->opt ),
                                  r.status, r.action );

        if( r.retunes )
            text += wxString::Format( wxS( ", %d retunes" ), r.retunes );

        text += wxS( ")\n" );
    }

    return text;
}

} // namespace


int RunPcbTune( JOB_PCB_TUNE& aJob, BOARD* aBoard, TOOL_MANAGER* aToolManager, REPORTER& aReporter,
                PROGRESS_REPORTER* aProgressReporter )
{
    aBoard->BuildConnectivity();

    std::vector<NETINFO_ITEM*> nets;

    for( const wxString& name : aJob.m_nets )
    {
        NETINFO_ITEM* net = aBoard->FindNet( name );

        if( !net )
        {
            aReporter.Report( wxString::Format( _( "No net '%s' on the board\n" ), name ), RPT_SEVERITY_ERROR );
            return CLI::EXIT_CODES::ERR_ARGS;
        }

        nets.push_back( net );
    }

    // A skew group: the nets matched to the longest of them
    std::optional<int64_t> groupLength;

    if( aJob.m_mode != MODE::SKEW && !aJob.m_length && nets.size() > 1 )
    {
        groupLength = 0;

        for( NETINFO_ITEM* net : nets )
            groupLength = std::max( *groupLength, lengthAsDrc( aBoard, net ) );
    }

    if( !aToolManager->FindTool( "pcbnew.Generators" ) )
        aToolManager->RegisterTool( new GENERATOR_TOOL );

    GENERATOR_TOOL* generatorTool = aToolManager->GetTool<GENERATOR_TOOL>();

    // Builds the push-and-shove router's world from this board
    generatorTool->Reset( TOOL_BASE::MODEL_RELOAD );

    PCB_TUNER               tuner( aJob, aBoard, generatorTool );
    std::vector<NET_RESULT> results;

    for( NETINFO_ITEM* net : nets )
        results.push_back( tuner.Tune( net, groupLength ) );

    // Refill the pours around the meanders, as `pcb drc --refill-zones` would: their old fills
    // would collide with them
    if( !aBoard->Zones().empty() )
    {
        if( !aToolManager->FindTool( ZONE_FILLER_TOOL_NAME ) )
            aToolManager->RegisterTool( new ZONE_FILLER_TOOL );

        // Its progress goes to stdout, which a report printed there must have to itself
        bool reportOnStdout = aJob.m_reportFormat == JOB_PCB_TUNE::REPORT_FORMAT::JSON
                              && aJob.m_reportFile.IsEmpty();

        aToolManager->GetTool<ZONE_FILLER_TOOL>()->FillAllZones(
                nullptr, reportOnStdout ? nullptr : aProgressReporter, true );
    }

    wxString outPath = aJob.m_output.IsEmpty() ? aJob.m_filename : aJob.m_output;

    try
    {
        IO_RELEASER<PCB_IO> pi( PCB_IO_MGR::FindPlugin( PCB_IO_MGR::KICAD_SEXP ) );
        pi->SaveBoard( outPath, aBoard );
    }
    catch( const IO_ERROR& ioe )
    {
        aReporter.Report( wxString::Format( _( "Error saving board file '%s'.\n%s" ), outPath, ioe.What() ),
                          RPT_SEVERITY_ERROR );
        return CLI::EXIT_CODES::ERR_UNKNOWN;
    }

    wxString report = aJob.m_reportFormat == JOB_PCB_TUNE::REPORT_FORMAT::JSON
                              ? wxString::FromUTF8( jsonReport( results ).dump( 2 ) + "\n" )
                              : textReport( results );

    if( aJob.m_reportFile.IsEmpty() )
    {
        // As UTF-8, and as the text it is, not a format
        fputs( report.utf8_str(), stdout );
        fflush( stdout );
    }
    else
    {
        wxFFile file( aJob.m_reportFile, wxS( "wb" ) );

        if( !file.IsOpened() || !file.Write( report, wxConvUTF8 ) )
        {
            aReporter.Report( wxString::Format( _( "Error writing report file '%s'\n" ), aJob.m_reportFile ),
                              RPT_SEVERITY_ERROR );
            return CLI::EXIT_CODES::ERR_UNKNOWN;
        }
    }

    for( const NET_RESULT& r : results )
    {
        if( r.status != wxS( "tuned" ) )
            return CLI::EXIT_CODES::ERR_RC_VIOLATIONS;
    }

    return CLI::EXIT_CODES::SUCCESS;
}
