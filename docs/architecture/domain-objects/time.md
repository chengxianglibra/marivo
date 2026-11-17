# Time in Analysis

## Instants, scopes, and clocks

An **Instant** is an exact point on the timeline. A **Civil Date** is a calendar
date interpreted in a business timezone. A **Timezone** supplies the rules that
relate local clock readings and calendar boundaries to instants. A date and a
timezone together can identify the beginning of a business day.

A **Time Scope** is an explicit interval selected for an observation. Marivo
uses half-open report intervals, including the start and excluding the end.
Adjacent intervals therefore meet at one boundary without overlapping there.

A **Contribution Clock** is the temporal field that determines when a source
contribution belongs to a Metric reduction. Booking time and payment time can
assign the same order to different periods. A composed Metric preserves the
clock of each contributing branch. A branch with no Contribution Clock retains
its own conditions when report time changes and broadcasts over applicable
existing report coordinates. A predicate on a time field can independently
restrict facts. Such a predicate supplies no report endpoint.

Source-time interpretation and report-time interpretation have separate
responsibilities. Source time establishes when a recorded event occurred or
which business date it denotes. Report time establishes the boundaries and
labels used to present observations. The report timezone cannot supply missing
source-time meaning.

## Calendar grain, period, and window

| Term | Definition | Example |
| --- | --- | --- |
| **Calendar Grain** | A calendar unit and width used to partition time. | Day, month, or an admitted subday width. |
| **Calendar Period** | One bounded bucket under a calendar interpretation. | August 2026 in Asia/Shanghai. |
| **Calendar Axis** | A finite selection of calendar periods with their grain, timezone, order, and boundaries. | The daily periods within August. |
| **Window** | A rule determining the contribution interval evaluated at a selected endpoint. | Trailing seven days or month to date. |
| **Window Endpoint** | The boundary supplied by the enclosing report or internal sample context for evaluating a window. | The end of the current internal daily sample. |
| **Reporting Endpoint** | The boundary of a report interval or bucket that can supply a Window Endpoint. | The excluded end of a daily output bucket. |

A calendar grain determines output buckets. A window determines which
contributions enter the calculation at each endpoint. A daily result can report
a trailing seven-day Metric. The overlap between its windows means one event
can contribute to several daily output values.

Internal time samples can use a Time Dimension different from the Contribution
Clock. An inner window can take its endpoint from the nearest enclosing time
sample. For example, creation-day samples can supply daily endpoints for a
windowed paid-revenue Metric, whose contributions still follow payment time.
Taking a window over completed daily samples and taking daily samples of a
windowed Metric therefore have different evaluation orders.

Changing the report grain preserves each reduction's Contribution Clock.
Refining a daily source total into hourly buckets requires information about
subday contributions. A daily total alone does not supply that information.
Changing the Contribution Clock from booking date to payment timestamp changes
contribution membership and can change totals even over the same report range.
Similarly, a calendar day follows civil boundaries, while an elapsed duration
follows the timeline. Daylight-saving changes can make their widths differ.

An explicit complete Calendar Axis supplies candidate periods. Actual output
coverage still depends on the requested Output Domain and observation rules.
Business holidays and fiscal periods have their own governed calendar data and
comparison mappings.

## Membership time, attribute time, and versions

**Membership Time** selects which versioned Entity Instances are represented
in a Population at a specified instant. **Attribute Time** selects the version
that supplies an attribute during classification or lookup. Each answers a
separate part of the question.

A **Snapshot** represents a business cross-section for a governed period.
Selecting a snapshot chooses that cross-section's identities and values.
A **Validity Interval** states when a representation of an Entity Instance is
effective, with an explicit convention for its bounds and open end.

The consuming question determines whether version selection is at an Instant
or immediately before an excluded endpoint. With daily snapshots, selection at
August 1 midnight in the snapshot timezone chooses August 1, while selection
immediately before that boundary chooses July 31. Each chooses the exact governed cross-section, with
absence preserved when that snapshot or member is missing. Last-known-value
carry-forward would require its own declared business meaning.

A customer's purchase can occur in July, population membership can be selected
at August 1, and region can be classified as of the purchase or as of August 1.
Those choices produce different analyses. A temporal anchor fixes one exact
selection boundary. A row-dependent Attribute Time obtains that boundary from
each contributing fact when the operation supports that interpretation.

Changing an observation period leaves independently fixed membership and
attribute selections governed by their own declarations. Reselecting them is
an explicit change to the Analysis Context.

## Time correspondence and evidence

A **Time Correspondence** specifies which periods from two observations should
be paired. Calendar offset and ordinal position express different rules.
“Previous month,” “the same weekday,” and “the corresponding fiscal period”
require the intended rule and period boundaries to be stated.

Ordinal correspondence follows the original selected period positions. Result
filtering preserves those positions, so removing the second day leaves a gap
between the first and third days. Renumbering the remaining rows would declare
a different correspondence. Calendar correspondence likewise retains the
captured period boundaries and timezone interpretation.

**Evaluation Time** records when a calculation completed. **Source Read Time**
records when access completed. **Commit Time** records when a result was saved.
These timestamps describe processing events. The Contribution Clock and source
coverage establish which business events the values represent.

The [temporal semantics contract](../temporal-semantics.md) owns exact boundary
rules. [Business time data](../semantic/temporal-authority.md) describes calendar
ownership, and [Entity versioning](../semantic/semantic-object-model.md#versioning-snapshot-and-validity)
defines snapshot and validity selection.
